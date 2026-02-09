#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

os.environ.setdefault("GOOGLE_API_KEY", "stress-test-placeholder")

import redis
from fastapi.testclient import TestClient
from google.oauth2 import id_token
from unittest.mock import patch

from billing import reserve_credits
from config import Settings
from continuity_stitch.core import VideoStitcher
from models import SessionLocal, User, Transaction, init_db
from server import app


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: str


def _print_result(result: ScenarioResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}: {result.details}")


@contextmanager
def _db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_user(balance: int) -> User:
    init_db()
    username = f"stress_tester_{uuid.uuid4()}@example.com"
    with _db_session() as db:
        user = User(username=username, balance=balance)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _cleanup_user(user_id: int, job_id: str) -> None:
    with _db_session() as db:
        db.query(Transaction).filter(Transaction.reference_id == job_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()


def _connect_redis(host: str, port: int) -> redis.Redis:
    return redis.Redis(host=host, port=port, db=0, decode_responses=True)


def scenario_queue_persistence(redis_client: redis.Redis, pause_seconds: int) -> ScenarioResult:
    job_id = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp(prefix="stress_queue_")
    path_a = os.path.join(tmp_dir, "a.mp4")
    path_c = os.path.join(tmp_dir, "c.mp4")
    for path in (path_a, path_c):
        with open(path, "wb") as handle:
            handle.write(b"not-a-real-video")

    payload = {
        "prompt": "stress test",
        "path_a": path_a,
        "path_c": path_c,
        "job_id": job_id,
        "style": "Test",
        "audio": "Test",
        "neg": "",
        "guidance": 1.0,
        "motion": 1,
        "user_id": 0,
    }

    item = json.dumps(payload)
    processing_queue = "continuity_jobs_processing"
    job_queue = "continuity_jobs"

    try:
        redis_client.ping()
    except redis.RedisError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ScenarioResult(
            "Queue Persistence",
            False,
            f"Redis unavailable: {exc}",
        )

    redis_client.lpush(job_queue, item)

    timeout_s = 20
    found_processing = False
    start = time.time()
    while time.time() - start < timeout_s:
        entries = redis_client.lrange(processing_queue, 0, -1)
        if item in entries:
            found_processing = True
            break
        time.sleep(0.5)

    if not found_processing:
        redis_client.lrem(job_queue, 1, item)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ScenarioResult(
            "Queue Persistence",
            False,
            "Worker did not move job into processing queue within timeout.",
        )

    print(
        f"Queue Persistence: job is in {processing_queue}. "
        "Stop the worker now to simulate a crash."
    )
    time.sleep(pause_seconds)

    remaining = redis_client.lrange(processing_queue, 0, -1)
    still_present = item in remaining

    redis_client.lrem(processing_queue, 1, item)
    redis_client.lrem(job_queue, 1, item)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    if still_present:
        return ScenarioResult(
            "Queue Persistence",
            True,
            "Job remained in processing queue after worker stop window.",
        )

    return ScenarioResult(
        "Queue Persistence",
        False,
        "Job was removed from processing queue before recovery check.",
    )


def scenario_billing_idempotency() -> ScenarioResult:
    cost = Settings.COST_PER_JOB
    user = _ensure_user(balance=cost * 3)
    job_id = str(uuid.uuid4())

    results: list[str] = []
    errors: list[str] = []

    def worker_call():
        try:
            reserve_credits(user.id, cost, job_id)
            results.append("ok")
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker_call) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with _db_session() as db:
        refreshed_user = db.query(User).filter(User.id == user.id).first()
        txn_count = (
            db.query(Transaction)
            .filter(
                Transaction.reference_id == job_id,
                Transaction.type == "reserve",
            )
            .count()
        )

    expected_balance = cost * 2
    passed = (
        refreshed_user is not None
        and refreshed_user.balance == expected_balance
        and txn_count == 1
    )

    _cleanup_user(user.id, job_id)

    details = (
        f"Balance={refreshed_user.balance if refreshed_user else 'missing'}, "
        f"expected={expected_balance}, reserve_txns={txn_count}, "
        f"errors={errors or 'none'}"
    )
    return ScenarioResult("Billing Idempotency", passed, details)


def scenario_invalid_payload_rejection(base_url: str) -> ScenarioResult:
    client = TestClient(app)

    payload = {
        "prompt": "stress test",
        "style": "Test",
        "audio_prompt": "Test",
        "negative_prompt": "",
        "guidance_scale": 1.0,
        "motion_strength": 1,
        "video_a_path": "/tmp/does-not-exist-a.mp4",
        "video_c_path": "/tmp/does-not-exist-c.mp4",
        "resolution": "720p",
    }

    with patch.object(id_token, "verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "stress@example.com",
            "iss": "accounts.google.com",
        }
        response = client.post(
            f"{base_url}/generate",
            json=payload,
            headers={"Authorization": "Bearer testtoken"},
        )

    if response.status_code == 400:
        return ScenarioResult(
            "Invalid Payload Rejection",
            True,
            f"Received 400 as expected: {response.json().get('detail')}",
        )

    return ScenarioResult(
        "Invalid Payload Rejection",
        False,
        f"Unexpected status {response.status_code}: {response.text}",
    )


def _list_orphan_dirs(prefix: str) -> set[str]:
    temp_root = tempfile.gettempdir()
    return {
        os.path.join(temp_root, name)
        for name in os.listdir(temp_root)
        if name.startswith(prefix)
    }


def _make_sample_videos(tmp_dir: str) -> list[str]:
    paths = []
    for idx in range(3):
        path = os.path.join(tmp_dir, f"sample_{idx}.mp4")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        paths.append(path)
    return paths


def scenario_resource_cleanup() -> ScenarioResult:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return ScenarioResult(
            "Resource Cleanup",
            False,
            "ffmpeg/ffprobe missing; cannot validate temp cleanup behavior.",
        )

    tmp_dir = tempfile.mkdtemp(prefix="stress_stitch_")
    output_path = os.path.join(tmp_dir, "out.mp4")
    try:
        inputs = _make_sample_videos(tmp_dir)

        before = _list_orphan_dirs("continuity_stitch_")
        child_code = (
            "import time\n"
            "from continuity_stitch.core import VideoStitcher\n"
            f"inputs = {inputs!r}\n"
            f"output = {output_path!r}\n"
            "stitcher = VideoStitcher(inputs, output)\n"
            "stitcher._temp_path('probe.txt')\n"
            "time.sleep(20)\n"
            "stitcher.stitch()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)
        proc.kill()
        proc.wait(timeout=5)

        after_kill = _list_orphan_dirs("continuity_stitch_")
        orphaned = list(after_kill - before)

        if not orphaned:
            return ScenarioResult(
                "Resource Cleanup",
                True,
                "No orphaned temp directories detected after SIGKILL.",
            )

        normal_output = os.path.join(tmp_dir, "out_normal.mp4")
        VideoStitcher(inputs, normal_output).stitch()

        remaining = [path for path in orphaned if os.path.exists(path)]
        if not remaining:
            return ScenarioResult(
                "Resource Cleanup",
                True,
                "Next run cleared orphaned temp directories.",
            )

        for path in remaining:
            shutil.rmtree(path, ignore_errors=True)

        return ScenarioResult(
            "Resource Cleanup",
            False,
            f"Orphaned temp dirs persisted: {remaining}",
        )
    except Exception as exc:
        return ScenarioResult(
            "Resource Cleanup",
            False,
            f"Error during cleanup test: {exc}",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuity reliability stress tests")
    parser.add_argument("--redis-host", default=os.getenv("REDIS_HOST", "localhost"))
    parser.add_argument("--redis-port", type=int, default=int(os.getenv("REDIS_PORT", "6379")))
    parser.add_argument("--pause-seconds", type=int, default=15)
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    results = []
    redis_client = _connect_redis(args.redis_host, args.redis_port)
    results.append(scenario_queue_persistence(redis_client, args.pause_seconds))
    results.append(scenario_billing_idempotency())
    results.append(scenario_invalid_payload_rejection(base_url))
    results.append(scenario_resource_cleanup())

    print("\nSummary")
    for result in results:
        _print_result(result)

    failed = [result for result in results if not result.passed]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
