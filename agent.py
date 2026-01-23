import os
import time
import logging
import tempfile
import json
from google import genai
from google.genai import types
from config import Settings
from utils import download_to_temp, download_blob, save_video_bytes, update_job_status, stitch_videos

logging.basicConfig(level=logging.INFO)

def analyze_only(path_a, path_c, job_id=None):
    update_job_status(job_id, "analyzing", 10, "Director starting analysis...")
    client = genai.Client(api_key=Settings.GOOGLE_API_KEY)
    
    try:
        file_a = client.files.upload(file=path_a)
        file_c = client.files.upload(file=path_c)
        
        while file_a.state.name == "PROCESSING" or file_c.state.name == "PROCESSING":
            time.sleep(1)

        prompt = "You are a director. Analyze Video A and Video C. Write a visual prompt for a 2-second transition (Video B) connecting A to C. Output ONLY the prompt."
        update_job_status(job_id, "analyzing", 30, "Director drafting transition...")
        
        res = client.models.generate_content(
            model="gemini-2.0-flash-exp", 
            contents=[prompt, file_a, file_c]
        )
        return {"prompt": res.text, "status": "success"}
    except Exception as e:
        return {"detail": str(e), "status": "error"}

def generate_only(prompt, path_a, path_c, job_id, style, audio, neg, guidance, motion):
    update_job_status(job_id, "generating", 50, "Production started (Veo 3.1)...")

    full_prompt = f"{style} style. {prompt} Soundtrack: {audio}"
    if neg:
        full_prompt += f" --no {neg}"
        
    try:
        if Settings.GCP_PROJECT_ID:
            client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)
            
            # Using generate_videos with config
            # Note: Guidance and Motion strength parameters would be used here if the model config supported them directly in this SDK version
            # For now we use the main prompt instructions.
            op = client.models.generate_videos(
                model='veo-3.1-generate-preview', 
                prompt=full_prompt, 
                config=types.GenerateVideosConfig(number_of_videos=1)
            )
            
            while not op.done:
                time.sleep(5)
                op = client.operations.get(op)

            if op.result and op.result.generated_videos:
                vid = op.result.generated_videos[0]
                bridge_path = None
                
                if vid.video.uri:
                    bridge_path = tempfile.mktemp(suffix=".mp4")
                    download_blob(vid.video.uri, bridge_path)
                elif vid.video.video_bytes:
                    bridge_path = save_video_bytes(vid.video.video_bytes)
                
                if bridge_path:
                    # --- PHASE 3: THE FINAL CUT ---
                    update_job_status(job_id, "stitching", 80, "Stitching Director's Cut (A+B+C)...")
                    final_cut_path = os.path.join("outputs", f"{job_id}_full_movie.mp4")
                    
                    try:
                        final_output = stitch_videos(path_a, bridge_path, path_c, final_cut_path)
                        # Update with the FULL MOVIE, not just the bridge
                        update_job_status(job_id, "completed", 100, "Done! Director's Cut Ready.", video_url=final_output)
                    except Exception as e:
                        # If stitch fails, fallback to just the bridge
                        logging.error(f"Stitch failed: {e}")
                        update_job_status(job_id, "completed", 100, "Stitch failed, showing bridge only.", video_url=bridge_path)
                    return
    except Exception as e:
        update_job_status(job_id, "error", 0, f"Error: {e}")
        return
        
    update_job_status(job_id, "error", 0, "Generation failed.")