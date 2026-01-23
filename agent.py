import os
import time
import logging
import tempfile
import hashlib
from google import genai
from google.genai import types
from config import Settings
from utils import download_to_temp, download_blob, save_video_bytes, update_job_status, stitch_videos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_file_hash(filepath):
    """Calculates MD5 hash of file to prevent duplicate uploads."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def get_or_upload_file(client, filepath):
    """Uploads file only if it doesn't already exist in Gemini (deduplication)."""
    file_hash = get_file_hash(filepath)
    
    # Check if file with this hash name already exists
    try:
        for f in client.files.list(config={'page_size': 50}):
            if f.display_name == file_hash and f.state.name == "ACTIVE":
                logger.info(f"♻️ Smart Cache Hit: Using existing file {file_hash}")
                return f
    except Exception:
        pass

    logger.info(f"wm Uploading new file: {filepath} (Hash: {file_hash})")
    return client.files.upload(file=filepath, config={'display_name': file_hash})

def analyze_videos(state):
    # Note: This function is kept for LangGraph compatibility if needed,
    # but the main logic is now in analyze_only below.
    return analyze_only(state['video_a_local_path'], state['video_c_local_path'], state.get('job_id'))

def analyze_only(path_a, path_c, job_id=None):
    update_job_status(job_id, "analyzing", 10, "Director checking file cache...")
    client = genai.Client(api_key=Settings.GOOGLE_API_KEY)
    
    try:
        # 1. Smart Upload
        file_a = get_or_upload_file(client, path_a)
        file_c = get_or_upload_file(client, path_c)
        
        # 2. Wait for processing
        while file_a.state.name == "PROCESSING" or file_c.state.name == "PROCESSING":
            update_job_status(job_id, "analyzing", 20, "Google is processing video geometry...")
            time.sleep(2)
            file_a = client.files.get(name=file_a.name)
            file_c = client.files.get(name=file_c.name)
        
        # 3. THE V2.7 'VFX DIRECTOR' PROMPT
        prompt = """
        You are a VFX Director specializing in surreal, seamless video morphing.
        
        Task: Analyze the visual composition, lighting, and primary shapes of Video A (Start) and Video C (End).
        Goal: Write a visual prompt for a 2-second intermediate video (Video B) that semantically transforms A into C.
        
        Strict Rules:
        1. DO NOT use words like "dissolve", "fade", "cut", or "transition".
        2. Describe a PHYSICAL transformation. How does the texture of A become the texture of C?
        3. Match the lighting evolution (e.g., "The golden hour light creates deep shadows that morph into...")
        4. Find a connecting shape (e.g., "The curve of the river flows upwards to match the curve of the jawline").
        
        Output: ONLY the final visual prompt text. Keep it under 40 words.
        """
        
        update_job_status(job_id, "analyzing", 30, "Director drafting creative morph...")
        
        res = client.models.generate_content(model="gemini-2.0-flash-exp", contents=[prompt, file_a, file_c])
        return {"prompt": res.text, "status": "success"}
    except Exception as e:
        logger.error(f"Analysis Error: {e}")
        return {"detail": str(e), "status": "error"}

def generate_only(prompt, path_a, path_c, job_id, style, audio, neg, guidance, motion):
    update_job_status(job_id, "generating", 50, "Production started (Veo 3.1)...")
    
    full_prompt = f"{style} style. {prompt} Soundtrack: {audio}"
    if neg:
        full_prompt += f" --no {neg}"
        
    try:
        if Settings.GCP_PROJECT_ID:
            client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)
            op = client.models.generate_videos(
                model='veo-3.1-generate-preview',
                prompt=full_prompt,
                config=types.GenerateVideosConfig(number_of_videos=1)
            )
            
            while not op.done:
                time.sleep(5)
                # Ideally check status here if possible, or just wait loop
            
            if op.result and op.result.generated_videos:
                vid = op.result.generated_videos[0]
                bridge_path = None
                
                if vid.video.uri:
                    bridge_path = tempfile.mktemp(suffix=".mp4")
                    download_blob(vid.video.uri, bridge_path)
                elif vid.video.video_bytes:
                    bridge_path = save_video_bytes(vid.video.video_bytes)
                
                if bridge_path:
                    update_job_status(job_id, "stitching", 80, "Stitching Director's Cut (A+B+C)...", video_url=bridge_path)
                    final_cut_path = os.path.join("outputs", f"{job_id}_merged_temp.mp4")
                    try:
                        final_output = stitch_videos(path_a, bridge_path, path_c, final_cut_path)
                        update_job_status(job_id, "completed", 100, "Done!", video_url=bridge_path, merged_video_url=final_output)
                    except Exception as e:
                        logger.error(f"Stitch failed: {e}")
                        update_job_status(job_id, "completed", 100, "Stitch failed, showing bridge.", video_url=bridge_path)
                    return

    except Exception as e:
        update_job_status(job_id, "error", 0, f"Error: {e}")
        return

    update_job_status(job_id, "error", 0, "Generation failed.")