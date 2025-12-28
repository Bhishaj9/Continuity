import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from google import genai
from gradio_client import Client, handle_file
import shutil
import requests
import tempfile
import os
import shutil
import requests
import tempfile

from dotenv import load_dotenv

load_dotenv()

# State Definition
class ContinuityState(TypedDict):
    video_a_url: str
    video_c_url: str
    user_notes: Optional[str]
    scene_analysis: Optional[str]
    veo_prompt: Optional[str]
    generated_video_url: Optional[str]
    video_a_local_path: Optional[str]
    video_c_local_path: Optional[str]

# Node 1: Analyst
def analyze_videos(state: ContinuityState) -> dict:
    print("--- Analyst Node (Director) ---")
    
    video_a_url = state['video_a_url']
    video_c_url = state['video_c_url']
    
    # Initialize Google GenAI Client
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    
    try:
        # Download videos to temp files for analysis
        def download_to_temp(url):
            print(f"Downloading: {url}")
            resp = requests.get(url, stream=True)
            resp.raise_for_status()
            suffix = os.path.splitext(url.split("/")[-1])[1] or ".mp4"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                shutil.copyfileobj(resp.raw, f)
                return f.name

        path_a = state.get('video_a_local_path')
        if not path_a:
             path_a = download_to_temp(video_a_url)
             
        path_c = state.get('video_c_local_path')
        if not path_c:
             path_c = download_to_temp(video_c_url)
        
        print("Uploading videos to Gemini...")
        file_a = client.files.upload(file=path_a)
        file_c = client.files.upload(file=path_c)
        
        # Wait for processing? Usually quick for small files, but good practice to check state if needed.
        # For simplicity in this agent, assuming ready or waiting implicitly. 
        # (Gemini 1.5 Flash usually processes quickly)

        prompt = """
        You are a film director. 
        Analyze the motion, lighting, and subject of the first video (Video A) and the second video (Video C). 
        Write a detailed visual prompt for a 2-second video (Video B) that smoothly transitions from the end of A to the start of C.
        Target Output: A single concise descriptive paragraph for the video generation model.
        """
        
        print("Generating transition prompt...")
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[prompt, file_a, file_c]
        )
        
        transition_prompt = response.text
        print(f"Generated Prompt: {transition_prompt}")
        
        # Cleanup uploaded files from local ? (Files on server stay for 48h or until deleted)
        # client.files.delete(name=file_a.name) 
        # client.files.delete(name=file_c.name)
        
        # We also need these local paths for the Generator node to extract frames!
        # Pass them in state or re-download? Better to pass paths if possible, but 
        # State definition expects URLs. We can add temp paths to state or re-download.
        # Let's add temp paths to state for efficiency.
        
        return {
            "scene_analysis": transition_prompt, 
            "veo_prompt": transition_prompt,
            "video_a_local_path": path_a,
            "video_c_local_path": path_c
        }

    except Exception as e:
        print(f"Error in Analyst: {e}")
        return {"scene_analysis": f"Error: {str(e)}", "veo_prompt": "Error"}


# Node 2: Generator (Wan 2.2 First Last Frame)
def generate_video(state: ContinuityState) -> dict:
    print("--- Generator Node (Wan 2.2) ---")
    
    prompt = state.get('veo_prompt', "")
    path_a = state.get('video_a_local_path')
    path_c = state.get('video_c_local_path')
    
    if not path_a or not path_c:
        # Fallback if dependencies failed or state clean
        # Re-download logic would go here, but assuming flow works
        return {"generated_video_url": "Error: Missing local video paths"}

    try:
        # Extract Frames
        import cv2
        from PIL import Image
        
        def get_frame(video_path, location="last"):
            cap = cv2.VideoCapture(video_path)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if location == "last":
                cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
            else: # first
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(frame_rgb)
            else:
                raise ValueError(f"Could not extract frame from {video_path}")

        print("Extracting frames...")
        img_start = get_frame(path_a, "last")
        img_end = get_frame(path_c, "first")
        
        # Save frames to temp files for Gradio Client (it handles file paths better than PIL objects usually)
        # Although client.predict might take PIL, handle_file is safer with paths.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_start:
            img_start.save(f_start, format="PNG")
            start_path = f_start.name
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f_end:
            img_end.save(f_end, format="PNG")
            end_path = f_end.name

        # Call Wan 2.2
        print("Initializing Wan Client...")
        client = Client("multimodalart/wan-2-2-first-last-frame")
        
        print(f"Generating transition with prompt: {prompt[:50]}...")
        # predict(start_image, end_image, prompt, negative_prompt, duration, steps, guide, guide2, seed, rand, api_name)
        result = client.predict(
            start_image_pil=handle_file(start_path),
            end_image_pil=handle_file(end_path),
            prompt=prompt,
            negative_prompt="blurry, distorted, low quality, static",
            duration_seconds=2.1,
            steps=20, # Default is often around 20-30 for good quality
            guidance_scale=5.0,
            guidance_scale_2=5.0,
            seed=42,
            randomize_seed=True,
            api_name="/generate_video"
        )
        
        # Clean up temp frames and videos
        try:
            os.remove(start_path)
            os.remove(end_path)
            os.remove(path_a)
            os.remove(path_c)
        except:
            pass

        # Parse valid result
        # Expected: ({'video': path, ...}, seed) or just path depending on version
        # Based on inspection: (generated_video_mp4, seed)
        video_out = result[0]
        if isinstance(video_out, dict) and 'video' in video_out:
             return {"generated_video_url": video_out['video']}
        elif isinstance(video_out, str) and os.path.exists(video_out):
             return {"generated_video_url": video_out}
        else:
             return {"generated_video_url": f"Error: Unexpected output {result}"}

    except Exception as e:
        print(f"Error in Generator: {e}")
        return {"generated_video_url": f"Error: {str(e)}"}


# Graph Construction
workflow = StateGraph(ContinuityState)

workflow.add_node("analyst", analyze_videos)
# workflow.add_node("prompter", draft_prompt) # Skipped, Analyst does extraction + prompting
workflow.add_node("generator", generate_video)

workflow.set_entry_point("analyst")

workflow.add_edge("analyst", "generator")
workflow.add_edge("generator", END)

app = workflow.compile()
