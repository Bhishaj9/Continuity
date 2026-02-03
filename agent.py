import os
import time
import logging
import tempfile
import hashlib
import json
from google import genai
from google.genai import types
from config import Settings
from utils import download_to_temp, download_blob, save_video_bytes, update_job_status, stitch_videos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Fix class to satisfy SDK requirements for polling
class GetOpRequest:
    def __init__(self, name):
        self.name = name


def get_file_hash(filepath):
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_or_upload_file(client, filepath):
    file_hash = get_file_hash(filepath)
    try:
        for f in client.files.list(config={'page_size': 50}):
            if f.display_name == file_hash and f.state.name == "ACTIVE":
                logger.info(f"♻️ Smart Cache Hit: {file_hash}")
                return f
    except Exception:
        pass
    return client.files.upload(file=filepath, config={'display_name': file_hash})


def analyze_only(path_a, path_c, job_id=None):
    update_job_status(job_id, "analyzing", 10, "Analyzing scenes...")
    client = genai.Client(api_key=Settings.GOOGLE_API_KEY)
    try:
        file_a = get_or_upload_file(client, path_a)
        file_c = get_or_upload_file(client, path_c)
        while file_a.state.name == "PROCESSING" or file_c.state.name == "PROCESSING":
            time.sleep(2)
            file_a = client.files.get(name=file_a.name)
            file_c = client.files.get(name=file_c.name)

        res = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=["Analyze Video A and C. Return JSON with analysis_a, analysis_c, visual_prompt_b.", file_a, file_c],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        text = res.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        data = json.loads(text)
        return {"prompt": data.get("visual_prompt_b", text), "status": "success"}
    except Exception as e:
        return {"detail": str(e), "status": "error"}


def generate_only(prompt, path_a, path_c, job_id, style, audio, neg, guidance, motion):
    update_job_status(job_id, "generating", 50, "Production started...")
    full_prompt = f"{style} style. {prompt} Soundtrack: {audio}"
    try:
        if not Settings.GCP_PROJECT_ID:
            raise Exception("GCP_PROJECT_ID missing.")
        client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)

        # Start Job
        op = client.models.generate_videos(
            model='veo-3.1-generate-preview', 
            prompt=full_prompt, 
            config=types.GenerateVideosConfig(number_of_videos=1)
        )
        
        # Extract ID and create the proxy object for the SDK
        op_name = op.name if hasattr(op, 'name') else str(op)
        request_proxy = GetOpRequest(op_name)
        logger.info(f"Polling Job: {op_name}")
        start_time = time.time()
        while True:
            if time.time() - start_time > 600:
                raise Exception("Timeout (10m).")
            
            # REFRESH logic using the proxy object
            try:
                op = client.operations.get(request_proxy)
            except Exception as e:
                logger.warning(f"Polling error: {e}")
                time.sleep(20)
                continue
            
            # Check Status
            is_done = getattr(op, 'done', False)
            if is_done:
                logger.info("Generation Done.")
                break
            
            logger.info("Waiting for Veo...")
            time.sleep(20)
        
        # Process Result
        res_val = op.result
        result = res_val() if callable(res_val) else res_val
        
        if result and (getattr(result, 'generated_videos', None) or 'generated_videos' in result):
            vid = result.generated_videos[0] if hasattr(result, 'generated_videos') else result['generated_videos'][0]
            bridge_path = tempfile.mktemp(suffix=".mp4")
            
            if hasattr(vid.video, 'uri') and vid.video.uri:
                download_blob(vid.video.uri, bridge_path)
            else:
                bridge_path = save_video_bytes(vid.video.video_bytes)
            
            update_job_status(job_id, "stitching", 85, "Stitching...")
            final_cut = os.path.join("outputs", f"{job_id}_final.mp4")
            merged_path = stitch_videos(path_a, bridge_path, path_c, final_cut)
            
            msg = "Done! (Merged)" if merged_path else "Done! (Bridge Only)"
            update_job_status(job_id, "completed", 100, msg, video_url=bridge_path, merged_video_url=merged_path)
        else:
            raise Exception("No video output.")
    except Exception as e:
        logger.error(f"Error: {e}")
        update_job_status(job_id, "error", 0, str(e))