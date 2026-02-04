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
    logger.info(f"⬆️ Uploading new file: {file_hash}")
    return client.files.upload(file=filepath, config={'display_name': file_hash})


def analyze_only(path_a, path_c, job_id=None):
    update_job_status(job_id, "analyzing", 10, "Director checking file cache...")
    client = genai.Client(api_key=Settings.GOOGLE_API_KEY)

    try:
        file_a = get_or_upload_file(client, path_a)
        file_c = get_or_upload_file(client, path_c)

        while file_a.state.name == "PROCESSING" or file_c.state.name == "PROCESSING":
            update_job_status(job_id, "analyzing", 20, "Google processing video...")
            time.sleep(2)
            file_a = client.files.get(name=file_a.name)
            file_c = client.files.get(name=file_c.name)

        prompt = """
        You are a VFX Director. Analyze Video A and Video C.
        Return a JSON object with exactly these keys:
        {
            "analysis_a": "Brief description of Video A",
            "analysis_c": "Brief description of Video C",
            "visual_prompt_b": "A surreal, seamless morphing prompt that transforms A into C."
        }
        """
        update_job_status(job_id, "analyzing", 30, "Director drafting creative morph...")

        res = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=[prompt, file_a, file_c],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        text = res.text.strip()
        if text.startswith("```json"): text = text[7:]
        elif text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()
        
        data = {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list): data = parsed[0] if len(parsed) > 0 else {}
            elif isinstance(parsed, dict): data = parsed
        except json.JSONDecodeError:
            logger.warning(f"JSON Parse Failed. Fallback to raw text.")
            pass

        return {
            "analysis_a": data.get("analysis_a", "Analysis unavailable."),
            "analysis_c": data.get("analysis_c", "Analysis unavailable."),
            "prompt": data.get("visual_prompt_b", text), 
            "status": "success"
        }

    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return {"detail": str(e), "status": "error"}


def generate_only(prompt, path_a, path_c, job_id, style, audio, neg, guidance, motion):
    try:
        update_job_status(job_id, "generating", 50, "Production started (Veo 3.1)...")
        full_prompt = f"{style} style. {prompt} Soundtrack: {audio}"
        if neg:
            full_prompt += f" --no {neg}"

        if not Settings.GCP_PROJECT_ID:
            raise Exception("GCP_PROJECT_ID missing.")
            
        client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)
        
        # 1. Start Job
        op = client.models.generate_videos(
            model='veo-3.1-generate-preview', 
            prompt=full_prompt, 
            config=types.GenerateVideosConfig(number_of_videos=1)
        )
        
        # 2. Extract ID String
        op_name = op.name if hasattr(op, 'name') else str(op)
        logger.info(f"Polling Job ID: {op_name}")
        
        # 3. Create Valid SDK Object for Polling
        polling_op = types.GenerateVideosOperation(name=op_name)

        start_time = time.time()
        while True:
            if time.time() - start_time > 600:
                raise Exception("Timeout (10m).")
            
            try:
                # Refresh logic: Pass the valid types.GenerateVideosOperation object
                refreshed_op = client.operations.get(polling_op)
                
                # Check status
                if hasattr(refreshed_op, 'done') and refreshed_op.done:
                    logger.info("Generation Done.")
                    op = refreshed_op 
                    break
                    
            except Exception as e:
                logger.warning(f"Polling error: {e}")
                time.sleep(20)
                continue
            
            logger.info("Waiting for Veo...")
            time.sleep(20)

        # 4. Result Extraction
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
            final_cut = os.path.join("outputs", f"{job_id}_merged_temp.mp4")
            merged_path = stitch_videos(path_a, bridge_path, path_c, final_cut)
            
            msg = "Done! (Merged)" if merged_path else "Done! (Bridge Only)"
            update_job_status(job_id, "completed", 100, msg, video_url=bridge_path, merged_video_url=merged_path)
        else:
            raise Exception("No video output.")

    except Exception as e:
        logger.error(f"Worker crashed: {e}")
        update_job_status(job_id, "error", 0, f"Error: {e}")

    finally:
        # Enforce Terminal State
        try:
            status_file = f"outputs/{job_id}.json"
            if os.path.exists(status_file):
                with open(status_file, "r") as f:
                    data = json.load(f)

                status = data.get("status")
                if status not in ["completed", "error"]:
                    logger.warning(f"Job {job_id} left in non-terminal state ({status}). Forcing error.")
                    update_job_status(job_id, "error", 0, "Job terminated unexpectedly.")
        except Exception as e:
            logger.error(f"Final safety net failed: {e}")
