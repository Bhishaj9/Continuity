import json
import os
import sys
import time
import uuid

import redis


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def redis_client_from_env():
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    return redis.Redis(host=host, port=port, db=db, decode_responses=True)


def ping_redis(client):
    test_key = f"smoke:test:{uuid.uuid4()}"
    test_value = f"value-{uuid.uuid4()}"
    client.set(test_key, test_value, ex=60)
    fetched = client.get(test_key)
    if fetched != test_value:
        fail("Redis set/get failed.")
    print("Redis ping set/get OK.")


def push_mock_job(client):
    job_id = str(uuid.uuid4())
    payload = {
        "prompt": "Smoke test prompt",
        "path_a": "/app/inputs/a.mp4",
        "path_c": "/app/inputs/c.mp4",
        "job_id": job_id,
        "style": "cinematic",
        "audio": "none",
        "neg": "",
        "guidance": 0,
        "motion": 0,
        "user_id": "smoke-test",
    }
    serialized = json.dumps(payload)
    initial_len = client.llen("continuity_jobs")
    client.lpush("continuity_jobs", serialized)
    after_len = client.llen("continuity_jobs")
    print(f"Pushed mock job {job_id}. Queue length {initial_len} -> {after_len}.")
    return serialized, after_len


def wait_for_worker_pickup(client, serialized, starting_len, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        current_len = client.llen("continuity_jobs")
        if current_len < starting_len:
            print("Worker appears to have popped a job from the queue.")
            return
        if serialized not in client.lrange("continuity_jobs", 0, -1):
            print("Mock job payload no longer in queue (worker likely picked it up).")
            return
        time.sleep(1)
    fail("Worker did not pick up the mock job within timeout.")


def verify_output_volume():
    output_dir = "/app/outputs"
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"smoke_test_{uuid.uuid4()}.txt")
    content = "smoke test output"
    with open(filename, "w", encoding="utf-8") as handle:
        handle.write(content)
    if not os.path.exists(filename):
        fail("Output file was not created in /app/outputs.")
    with open(filename, "r", encoding="utf-8") as handle:
        read_back = handle.read()
    if read_back != content:
        fail("Output file content mismatch in /app/outputs.")
    print(f"Output volume write OK: {filename}")


def main():
    try:
        client = redis_client_from_env()
        ping_redis(client)
        serialized, queue_len = push_mock_job(client)
        wait_for_worker_pickup(client, serialized, queue_len)
        verify_output_volume()
        print("Smoke test completed successfully.")
    except redis.RedisError as exc:
        fail(f"Redis error: {exc}")


if __name__ == "__main__":
    main()
