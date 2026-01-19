import os
import time
import logging
import json
import subprocess
import tempfile
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

# Import unified SDK
from google import genai
from google.genai import types

# Import other clients
from gradio_client import Client, handle_file
from huggingface_hub import InferenceClient

# Import refactored modules
from config import Settings
from utils import download_to_temp, download_blob, save_video_bytes, update_job_status

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State Definition
class ContinuityState(TypedDict):
    job_id: Optional[str]
    video_a_url: str
    video_c_url: str
    style: Optional[str]
    audio_prompt: Optional[str]
    user_notes: Optional[str]
    scene_analysis: Optional[str]
    veo_prompt: Optional[str]
    generated_video_url: Optional[str]
    video_a_local_path: Optional[str]
    video_c_local_path: Optional[str]

def generate_audio(prompt: str) -> Optional[str]:
    """Generates audio SFX using AudioLDM."""
    try:
        logger.info(f"🎵 Generating Audio for: {prompt[:30]}...")
        # Use a model good for SFX/Atmosphere
        client = InferenceClient("cvssp/audioldm-12.8k-caps", token=Settings.HF_TOKEN)
        # AudioLDM takes a text prompt and returns bytes
        audio_bytes = client.text_to_audio(
            prompt, 
            guidance_scale=2.5, 
            num_inference_steps=10
        )
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
            f.write(audio_bytes)
            return f.name
            
    except Exception as e:
        logger.error(f"Audio generation failed: {e}")
        return None

def merge_audio_video(video_path: str, audio_path: str) -> str:
    """Merges video and audio using ffmpeg."""
    if not audio_path:
        return video_path
        
    try:
        output_path = video_path.replace(".mp4", "_merged.mp4")
        logger.info(f"🎬 Merging Audio & Video: {video_path} + {audio_path}")
        
        # ffmpeg command: -i video -i audio -c:v copy -c:a aac -shortest output
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_path
    except Exception as e:
        logger.error(f"FFmpeg Merge Failed: {e}")
        return video_path

# --- NODE 1: ANALYST ---
def analyze_videos(state: ContinuityState) -> dict:
    logger.info("--- 🧐 Analyst Node (Director) ---")
    job_id = state.get("job_id")
    
    update_job_status(job_id, "analyzing", 10, "Director starting analysis...")

    video_a_url = state['video_a_url']
    video_c_url = state['video_c_url']
    style = state.get('style', 'Cinematic')
    
    # 1. Prepare Files
    try:
        path_a = state.get('video_a_local_path')
        if not path_a:
            path_a = download_to_temp(video_a_url)

        path_c = state.get('video_c_local_path')
        if not path_c:
            path_c = download_to_temp(video_c_url)
    except Exception as e:
        error_msg = f"Download failed: {e}"
        logger.error(error_msg)
        update_job_status(job_id, "error", 0, error_msg)
        return {"scene_analysis": "Error downloading", "veo_prompt": "Smooth cinematic transition"}

    update_job_status(job_id, "analyzing", 20, "Director analyzing motion and lighting...")

    # 2. Try Gemini 2.0 (With Retry and Wait Loop)
    client = genai.Client(api_key=Settings.GOOGLE_API_KEY)
    transition_prompt = None
    retries = 3
    for attempt in range(retries):
        try:
            if attempt > 0:
                 update_job_status(job_id, "analyzing", 20, f"Retrying analysis (Attempt {attempt+1})...")

            file_a = client.files.upload(file=path_a)
            file_c = client.files.upload(file=path_c)

            while file_a.state.name == "PROCESSING":
                time.sleep(1)
                file_a = client.files.get(name=file_a.name)
                
            while file_c.state.name == "PROCESSING":
                time.sleep(1)
                file_c = client.files.get(name=file_c.name)
            
            prompt_text = f"""
            You are a film director. 
            Analyze the motion, lighting, and subject of the first video (Video A) and the second video (Video C). 
            Write a detailed visual prompt for a 2-second video (Video B) that smoothly transitions from the end of A to the start of C.
            
            STYLE INSTRUCTION: The user wants the style to be "{style}". Ensure the visual description reflects this style.
            
            Target Output: A single concise descriptive paragraph for the video generation model.
            """
            
            update_job_status(job_id, "analyzing", 30, "Director writing scene transition...")
            
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp", 
                contents=[prompt_text, file_a, file_c]
            )
            transition_prompt = response.text
            logger.info(f"Generated Prompt: {transition_prompt}")
            break # Success
        except Exception as e:
            time.sleep(2)
            if attempt == retries - 1:
                logger.error(f"Gemini analysis failed: {e}")

    # 3. Fallback: Default Prompt
    if not transition_prompt:
        logger.warning("Gemini analysis failed. Using default transition prompt.")
        transition_prompt = "Smooth cinematic transition with motion blur matching the scenes."
            
    update_job_status(job_id, "generating", 40, "Director prompt ready. Starting generation...")
    
    return {
        "scene_analysis": transition_prompt, 
        "veo_prompt": transition_prompt,
        "video_a_local_path": path_a,
        "video_c_local_path": path_c
    }

# --- NODE 2: GENERATOR ---
def generate_video(state: ContinuityState) -> dict:
    logger.info("--- 🎥 Generator Node ---")
    job_id = state.get("job_id")
    
    prompt = state.get('veo_prompt', "")
    path_a = state.get('video_a_local_path')
    path_c = state.get('video_c_local_path')
    
    update_job_status(job_id, "generating", 50, "Veo initializing...")
    
    if not path_a or not path_c:
        error_msg = "Error: Missing local video paths"
        update_job_status(job_id, "error", 0, error_msg)
        return {}

    local_path = None

    # --- ATTEMPT 1: GOOGLE VEO ---
    try:
        logger.info("⚡ Initializing Google Veo (Unified SDK)...")
        if Settings.GCP_PROJECT_ID:
            client = genai.Client(
                vertexai=True, 
                project=Settings.GCP_PROJECT_ID, 
                location=Settings.GCP_LOCATION
            )
            
            logger.info(f"Generating with Veo... Prompt: {prompt[:30]}...")
            update_job_status(job_id, "generating", 60, "Veo generating video (this takes ~60s)...")
            
            operation = client.models.generate_videos(
                model='veo-2.0-generate-001',
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                )
            )
            
            while not operation.done:
                time.sleep(5)
                operation = client.operations.get(operation)
                
            if operation.result and operation.result.generated_videos:
                video_result = operation.result.generated_videos[0]
                
                # CASE 1: URI (GCS Bucket)
                if hasattr(video_result.video, 'uri') and video_result.video.uri:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                        local_path = f.name
                    download_blob(video_result.video.uri, local_path)
                
                # CASE 2: RAW BYTES (Direct Return)
                elif hasattr(video_result.video, 'video_bytes') and video_result.video.video_bytes:
                    logger.info("Veo returned raw bytes. Saving...")
                    local_path = save_video_bytes(video_result.video.video_bytes)
                
            else:
                logger.warning("Veo operation completed with no result.")
                
        else:
            logger.warning("⚠️ GCP_PROJECT_ID not set. Skipping Veo.")
            
    except Exception as e:
        logger.warning(f"⚠️ Veo Failed: {e}")

    # --- ATTEMPT 2: SVD FALLBACK (Free) ---
    if not local_path:
         # SVD Fallback logic omitted for brevity in user request, but I should probably keep it if it was there? 
         # The user provided code says:
         # if not local_path: update_job_status(job_id, "error", 0, "Video generation failed.") return {}
         # but the previous version had SVD.
         # User's text: "# --- ATTEMPT 2: SVD FALLBACK --- if not local_path: update_job_status(job_id, "error", 0, "Video generation failed.") return {}"
         # This suggests the user wants to REMOVE SVD fallback or just didn't include it fully.
         # "Please OVERWRITE Continuity/agent.py with the corrected code below."
         # The code below puts an abrupt error if not local_path.
         # I will stick to what the user provided, which simplifies the generator.
         update_job_status(job_id, "error", 0, "Video generation failed (Veo).")
         return {}

    # --- AUDIO & MERGE ---
    update_job_status(job_id, "generating", 90, "Generating audio SFX...")
    audio_path = generate_audio(prompt)
    
    if audio_path:
        update_job_status(job_id, "generating", 95, "Merging audio and video...")
        local_path = merge_audio_video(local_path, audio_path)
    
    update_job_status(job_id, "completed", 100, "Done!", video_url=local_path)
    return {"generated_video_url": local_path}

# Graph Construction
workflow = StateGraph(ContinuityState)
workflow.add_node("analyst", analyze_videos)
workflow.add_node("generator", generate_video)
workflow.set_entry_point("analyst")
workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)
app = workflow.compile()

# --- SERVER COMPATIBILITY WRAPPERS ---
def analyze_only(state_or_path_a, path_c=None, job_id=None, style="Cinematic"):
    if isinstance(state_or_path_a, str) and path_c:
        state = {
            "job_id": job_id,
            "video_a_url": "local",
            "video_c_url": "local",
            "video_a_local_path": state_or_path_a,
            "video_c_local_path": path_c,
            "style": style
        }
    else:
        state = state_or_path_a if isinstance(state_or_path_a, dict) else state_or_path_a.dict()
        if job_id and "job_id" not in state:
            state["job_id"] = job_id
        # Ensure style is in state
        if "style" not in state:
            state["style"] = style

    result = analyze_videos(state)
    return {"prompt": result.get("scene_analysis"), "status": "success"}

def generate_only(prompt, path_a, path_c, job_id=None, style="Cinematic"):
    state = {
        "job_id": job_id,
        "video_a_url": "local",
        "video_c_url": "local",
        "video_a_local_path": path_a,
        "video_c_local_path": path_c,
        "veo_prompt": prompt,
        "style": style
    }
    return generate_video(state)
