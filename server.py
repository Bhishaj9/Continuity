from fastapi import FastAPI, HTTPException, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import shutil
import uuid

# Import from the subpackage as before
from continuity_agent.agent import app as continuity_graph

app = FastAPI(title="Continuity", description="AI Video Bridging Service")

# 1. Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Setup Static Files for Outputs
OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

@app.post("/generate-transition")
async def generate_transition(
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...),
    prompt: str = Form("Cinematic transition")
):
    try:
        # Generate unique ID for this request
        request_id = str(uuid.uuid4())
        
        # Save inputs
        # Preserve extension
        ext_a = os.path.splitext(video_a.filename)[1] or ".mp4"
        ext_c = os.path.splitext(video_c.filename)[1] or ".mp4"
        
        path_a = os.path.join(OUTPUT_DIR, f"{request_id}_a{ext_a}")
        path_c = os.path.join(OUTPUT_DIR, f"{request_id}_c{ext_c}")
        
        with open(path_a, "wb") as buffer:
            shutil.copyfileobj(video_a.file, buffer)
            
        with open(path_c, "wb") as buffer:
            shutil.copyfileobj(video_c.file, buffer)
            
        # Initialize State with LOCAL PATHS
        # We don't need URLs for the new logic, but state definition might map them.
        # agent.py logic checks video_a_local_path if present.
        initial_state = {
            "video_a_url": "local_upload", # Placeholder
            "video_c_url": "local_upload", 
            "user_notes": prompt,
            "veo_prompt": prompt,
            "video_a_local_path": os.path.abspath(path_a),
            "video_c_local_path": os.path.abspath(path_c),
            "generated_video_url": "", 
            "status": "started"
        }
        
        # Invoke Agent
        result = continuity_graph.invoke(initial_state)
        
        # The agent returns 'generated_video_url' which is a local absolute path (e.g., from tempfile or cache)
        # We need to copy/move this to our STATIC directory to serve it.
        gen_path = result.get("generated_video_url")
        
        if not gen_path or "Error" in gen_path:
            raise HTTPException(status_code=500, detail=f"Generation failed: {gen_path}")
            
        # Copy generated video to outputs
        final_filename = f"{request_id}_bridge.mp4"
        final_output_path = os.path.join(OUTPUT_DIR, final_filename)
        
        shutil.copy(gen_path, final_output_path)
        
        # Return URL relative to server root
        return {"video_url": f"/outputs/{final_filename}"}

    except Exception as e:
        print(f"Server Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
