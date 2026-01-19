import os
import time
import logging
import json
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

# Import unified SDK
from google import genai
from google.genai import types

# Import other clients
from groq import Groq
from gradio_client import Client, handle_file

# Import refactored modules
from config import Settings
from utils import download_to_temp, download_blob, save_video_bytes, update_job_status

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State Definition
class ContinuityState(TypedDict):
    job_id: Optional[str] # Added job_id
    video_a_url: str
    video_c_url: str
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
            logger.info(f"Uploading videos to Gemini... (Attempt {attempt+1})")
            if attempt > 0:
                 update_job_status(job_id, "analyzing", 20, f"Retrying analysis (Attempt {attempt+1})...")

            file_a = client.files.upload(file=path_a)
            file_c = client.files.upload(file=path_c)

            # --- WAIT FOR ACTIVE STATE ---
            logger.info("Waiting for video processing...")
            while file_a.state.name == "PROCESSING":
                time.sleep(2)
                file_a = client.files.get(name=file_a.name)
                
            while file_c.state.name == "PROCESSING":
                time.sleep(2)
                file_c = client.files.get(name=file_c.name)
                
            if file_a.state.name != "ACTIVE" or file_c.state.name != "ACTIVE":
                logger.error(f"File state issue. A: {file_a.state.name}, C: {file_c.state.name}")
                raise Exception("Gemini files not active.")
            
            prompt_text = """
            You are a film director. 
            Analyze the motion, lighting, and subject of the first video (Video A) and the second video (Video C). 
            Write a detailed visual prompt for a 2-second video (Video B) that smoothly transitions from the end of A to the start of C.
            Target Output: A single concise descriptive paragraph for the video generation model.
            """
            
            logger.info("Generating transition prompt...")
            update_job_status(job_id, "analyzing", 30, "Director writing scene transition...")
            
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp", 
                contents=[prompt_text, file_a, file_c]
            )
            transition_prompt = response.text
            logger.info(f"Generated Prompt: {transition_prompt}")
            break # Success
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 30 * (attempt + 1)
                logger.warning(f"⚠️ Gemini Quota 429. Retrying in {wait}s...")
                update_job_status(job_id, "analyzing", 25, f"High traffic, retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"⚠️ Gemini Error: {e}")
                break

    # 3. Fallback: Groq (Updated Model)
    if not transition_prompt:
        logger.info("Switching to Llama 3.2 (Groq) Fallback...")
        update_job_status(job_id, "analyzing", 35, "Using backup director (Llama 3.2)...")
        try:
            groq_client = Groq(api_key=Settings.GROQ_API_KEY)
            fallback_prompt = "Create a smooth, cinematic visual transition that bridges two scenes."
            completion = groq_client.chat.completions.create(
                model="llama-3.2-90b-vision-preview",
                messages=[{"role": "user", "content": f"Refine this into a video prompt: {fallback_prompt}"}]
            )
            transition_prompt = completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ Groq also failed: {e}")
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
        return {"generated_video_url": error_msg}

    # --- ATTEMPT 1: GOOGLE VEO ---
    try:
        logger.info("⚡ Initializing Google Veo (Unified SDK)...")
        project_id = Settings.GCP_PROJECT_ID
        location = Settings.GCP_LOCATION
        
        if project_id:
            client = genai.Client(
                vertexai=True, 
                project=project_id, 
                location=location
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
            
            logger.info(f"Waiting for Veo operation {operation.name}...")
            while not operation.done:
                time.sleep(10)
                # Pass operation object, not name
                operation = client.operations.get(operation)
                logger.info("...still generating...")
                
            if operation.result and operation.result.generated_videos:
                video_result = operation.result.generated_videos[0]
                
                local_path = None
                # CASE 1: URI (GCS Bucket)
                if hasattr(video_result.video, 'uri') and video_result.video.uri:
                    gcs_uri = video_result.video.uri
                    logger.info(f"Veo output saved to GCS: {gcs_uri}")
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
                        local_path = f.name
                    
                    download_blob(gcs_uri, local_path)
                    logger.info(f"✅ Veo Video Downloaded (from GCS): {local_path}")
                
                # CASE 2: RAW BYTES (Direct Return)
                elif hasattr(video_result.video, 'video_bytes') and video_result.video.video_bytes:
                    logger.info("Veo returned raw bytes. Saving to local file...")
                    local_path = save_video_bytes(video_result.video.video_bytes)
                    logger.info(f"✅ Veo Video Saved (from Bytes): {local_path}")
                
                else:
                    logger.warning(f"Veo operation completed but no URI/Bytes found. Result: {video_result}")
                
                if local_path:
                    update_job_status(job_id, "completed", 100, "Done!", video_url=local_path)
                    return {"generated_video_url": local_path}

            else:
                logger.warning("Veo operation completed with no result.")
                
        else:
            logger.warning("⚠️ GCP_PROJECT_ID not set. Skipping Veo.")
            
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "PERMISSION_DENIED" in err_str:
            logger.warning("⚠️ Veo Permission Error. Please ensure the 'Vertex AI API' is enabled in your Google Cloud Console.")
        logger.warning(f"⚠️ Veo Failed: {e}")

    # --- ATTEMPT 2: SVD FALLBACK (Free) ---
    logger.info("🔄 Switching to SVD Fallback...")
    update_job_status(job_id, "generating", 60, "Switching to SVD fallback...")
    
    try:
        import cv2
        from PIL import Image
        
        def get_frame(video_path):
            cap = cv2.VideoCapture(video_path)
            ret, frame = cap.read()
            cap.release()
            if ret: return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            return None

        img_start = get_frame(path_a)
        if img_start is None: raise ValueError("Could not read start frame for SVD")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_start:
            img_start.save(f_start, format="PNG")
            start_path = f_start.name
            
        client = Client("multimodalart/stable-video-diffusion")
        
        update_job_status(job_id, "generating", 70, "SVD generating video...")
        result = client.predict(
            handle_file(start_path),
            0.0, 0.0, 1, 25,
            api_name="/video" 
        )
        logger.info(f"✅ SVD Generated: {result}")
        
        # --- FIX: ROBUST PARSING FOR SVD ---
        # SVD often returns ({'video': path}, metadata) or just path
        final_path = None
        if isinstance(result, tuple):
            if isinstance(result[0], dict) and 'video' in result[0]:
                final_path = result[0]['video']
            else:
                final_path = result[0]
        elif isinstance(result, dict) and 'video' in result:
            final_path = result['video']
        else:
            final_path = result
        
        update_job_status(job_id, "completed", 100, "Done (SVD)!", video_url=final_path)
        return {"generated_video_url": final_path}
        
    except Exception as e:
        logger.error(f"❌ All Generators Failed. Error: {e}")
        update_job_status(job_id, "error", 0, f"All generation failed: {e}")
        return {"generated_video_url": f"Error: {str(e)}"}

# Graph Construction
workflow = StateGraph(ContinuityState)
workflow.add_node("analyst", analyze_videos)
workflow.add_node("generator", generate_video)
workflow.set_entry_point("analyst")
workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)
app = workflow.compile()

# --- SERVER COMPATIBILITY WRAPPERS ---
def analyze_only(state_or_path_a, path_c=None, job_id=None):
    if isinstance(state_or_path_a, str) and path_c:
        state = {
            "job_id": job_id,
            "video_a_url": "local",
            "video_c_url": "local",
            "video_a_local_path": state_or_path_a,
            "video_c_local_path": path_c
        }
    else:
        state = state_or_path_a if isinstance(state_or_path_a, dict) else state_or_path_a.dict()
        if job_id and "job_id" not in state:
            state["job_id"] = job_id

    result = analyze_videos(state)
    return {"prompt": result.get("scene_analysis"), "status": "success"}

def generate_only(prompt, path_a, path_c, job_id=None):
    state = {
        "job_id": job_id,
        "video_a_url": "local",
        "video_c_url": "local",
        "video_a_local_path": path_a,
        "video_c_local_path": path_c,
        "veo_prompt": prompt
    }
    return generate_video(state)
