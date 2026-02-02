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

        prompt = "You are a VFX Director. Analyze Video A and Video C. Return JSON with keys: analysis_a, analysis_c, visual_prompt_b."
        
        res = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=[prompt, file_a, file_c],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        text = res.text.strip()
        if text.startswith("```json"): text = text[7:]
        elif text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        
        try: data = json.loads(text.strip())
        except: data = {}
        if isinstance(data, list): data = data[0] if len(data) > 0 else {}
        return {
            "analysis_a": data.get("analysis_a", ""),
            "analysis_c": data.get("analysis_c", ""),
            "prompt": data.get("visual_prompt_b", text),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return {"detail": str(e), "status": "error"}


def generate_only(prompt, path_a, path_c, job_id, style, audio, neg, guidance, motion):
    update_job_status(job_id, "generating", 50, "Production started (Veo 3.1)...")
    full_prompt = f"{style} style. {prompt} Soundtrack: {audio}"
    if neg:
        full_prompt += f" --no {neg}"

    job_failed = False
    try:
        if Settings.GCP_PROJECT_ID:
            client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)
            
            # 1. Start Job
            op = client.models.generate_videos(
                model='veo-3.1-generate-preview', 
                prompt=full_prompt, 
                config=types.GenerateVideosConfig(number_of_videos=1)
            )
            
            # 2. Extract ID String
            op_name = str(op)
            if hasattr(op, 'name'): op_name = op.name
            elif isinstance(op, dict) and 'name' in op: op_name = op['name']
            
            logger.info(f"Tracking ID: {op_name}")
            
            # 3. Poll for Completion
            start_time = time.time()
            while True:
                if time.time() - start_time > 300: 
                    raise Exception("Generation timed out.")
                
                try:
                    # FIX: Use positional argument for get()
                    current_op = client.operations.get(op_name)
                except Exception as e:
                    logger.warning(f"Refresh failed: {e}")
                    time.sleep(10)
                    continue
                
                # Check Status
                is_done = False
                if hasattr(current_op, 'done'): is_done = current_op.done
                elif isinstance(current_op, dict): is_done = current_op.get('done')
                
                if is_done:
                    logger.info("Job DONE.")
                    op = current_op 
                    break
                
                logger.info("Waiting for Veo...")
                time.sleep(10)
            
            # 4. Get Result
            result = None
            if hasattr(op, 'result'):
                try: 
                    res_val = op.result
                    if callable(res_val): result = res_val()
                    else: result = res_val
                except: pass
            elif isinstance(op, dict): 
                result = op.get('result')
            
            generated_videos = None
            if result:
                if hasattr(result, 'generated_videos'): generated_videos = result.generated_videos
                elif isinstance(result, dict): generated_videos = result.get('generated_videos')
            
            if generated_videos:
                vid = generated_videos[0]
                bridge_path = None
                
                uri = getattr(vid.video, 'uri', None) if hasattr(vid, 'video') else vid.get('video', {}).get('uri')
                video_bytes = getattr(vid.video, 'video_bytes', None) if hasattr(vid, 'video') else vid.get('video', {}).get('video_bytes')

                if uri:
                    bridge_path = tempfile.mktemp(suffix=".mp4")
                    download_blob(uri, bridge_path)
                elif video_bytes:
                    bridge_path = save_video_bytes(video_bytes)
                
                if bridge_path:
                    update_job_status(job_id, "stitching", 85, "Stitching...", video_url=bridge_path)
                    final_cut = os.path.join("outputs", f"{job_id}_merged_temp.mp4")
                    merged_path = stitch_videos(path_a, bridge_path, path_c, final_cut)
                    
                    msg = "Done! (Merged)" if merged_path else "Done! (Bridge Only)"
                    update_job_status(job_id, "completed", 100, msg, video_url=bridge_path, merged_video_url=merged_path)
                    return
            else:
                raise Exception("No videos returned.")
        else:
             raise Exception("GCP_PROJECT_ID not set.")

    except Exception as e:
        logger.error(f"Gen Fatal: {e}")
        update_job_status(job_id, "error", 0, f"Error: {e}")
        job_failed = True
    finally:
        if not job_failed:
            try:
                with open(f"outputs/{job_id}.json", "r") as f:
                    if json.load(f).get("status") not in ["completed", "error"]:
                        update_job_status(job_id, "error", 0, "Job timed out.")
            except: pass