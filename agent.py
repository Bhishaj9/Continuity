import os
import time
import logging
import json
import tempfile
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
# Import unified SDK
from google import genai
from google.genai import types

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
    
    # 2. Try Gemini 2.0 (With Retry)
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
            
    if not transition_prompt:
        transition_prompt = "Smooth cinematic transition with motion blur matching the scenes."
            
    update_job_status(job_id, "generating", 40, "Director prompt ready. Starting generation...")
    return { "scene_analysis": transition_prompt, "veo_prompt": transition_prompt, "video_a_local_path": path_a, "video_c_local_path": path_c }

# --- NODE 2: GENERATOR ---
def generate_video(state: ContinuityState) -> dict:
    logger.info("--- 🎥 Generator Node ---")
    job_id = state.get("job_id")
    visual_prompt = state.get('veo_prompt', "")
    audio_context = state.get('audio_prompt', "Realistic ambient sound")

    # Merge Prompts for Veo 3.1
    # Veo 3.1 understands audio instructions within the main prompt
    full_prompt = f"{visual_prompt} Soundtrack: {audio_context}"
    update_job_status(job_id, "generating", 50, "Veo initializing...")
    
    # Check GCP Project ID
    if not Settings.GCP_PROJECT_ID:
        error_msg = "GCP_PROJECT_ID not set. Veo requires Vertex AI."
        logger.error(error_msg)
        update_job_status(job_id, "error", 0, error_msg)
        return {}

    local_path = None
    # --- ATTEMPT: GOOGLE VEO 3.1 (With Native Audio) ---
    try:
        logger.info("⚡ Initializing Google Veo 3.1 (Unified SDK)...")
        client = genai.Client(vertexai=True, project=Settings.GCP_PROJECT_ID, location=Settings.GCP_LOCATION)
        
        logger.info(f"Generating with Veo 3.1... Prompt: {full_prompt[:50]}...")
        update_job_status(job_id, "generating", 60, f"Veo 3.1 generating with audio style: '{audio_context}'...")
        
        # Veo 3.1 supports native audio generation
        operation = client.models.generate_videos(
            model='veo-3.1-generate-preview',
            prompt=full_prompt,
            config=types.GenerateVideosConfig(
                number_of_videos=1,
            )
        )
        
        while not operation.done:
            time.sleep(5)
            operation = client.operations.get(operation)
            
        if operation.result and operation.result.generated_videos:
            video_result = operation.result.generated_videos[0]
            
            # Handle URI (GCS)
            if hasattr(video_result.video, 'uri') and video_result.video.uri:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                    local_path = f.name
                download_blob(video_result.video.uri, local_path)
            # Handle Bytes
            elif hasattr(video_result.video, 'video_bytes') and video_result.video.video_bytes:
                local_path = save_video_bytes(video_result.video.video_bytes)
        else:
            logger.warning("Veo operation completed with no result.")
            
    except Exception as e:
        logger.warning(f"⚠️ Veo Failed: {e}")
        update_job_status(job_id, "error", 0, f"Video generation failed: {e}")
        return {}
        
    if not local_path:
         update_job_status(job_id, "error", 0, "Video generation failed (Veo 3.1).")
         return {}
         
    # Audio is now native, so we skip separate audio generation!
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
    if isinstance(state_or_path_a, str):
        state = {
            "job_id": job_id,
            "video_a_url": "local",
            "video_c_url": "local",
            "video_a_local_path": state_or_path_a, 
            "video_c_local_path": path_c,
            "style": style
        }
    else:
        state = state_or_path_a
        state["job_id"] = job_id
        state["style"] = style
        
    result = analyze_videos(state)
    return {"prompt": result.get("scene_analysis"), "status": "success"}

def generate_only(prompt, path_a, path_c, job_id=None, style="Cinematic", audio_prompt="Cinematic ambient sound"):
    state = {
        "job_id": job_id,
        "video_a_url": "local",
        "video_c_url": "local",
        "video_a_local_path": path_a,
        "video_c_local_path": path_c,
        "veo_prompt": prompt,
        "style": style,
        "audio_prompt": audio_prompt # Pass the new parameter to state
    }
    return generate_video(state)