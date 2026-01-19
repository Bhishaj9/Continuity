from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import os
import shutil
import uuid
import json
from agent import analyze_only, generate_only

app = FastAPI(title="Continuity", description="AI Video Bridging Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

@app.get("/")
def read_root():
    return FileResponse("stitch_continuity_dashboard/code.html")

@app.post("/analyze")
def analyze_endpoint(
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...)
):
    # Changed to 'def' to run in threadpool (prevent blocking)
    try:
        request_id = str(uuid.uuid4())
        ext_a = os.path.splitext(video_a.filename)[1] or ".mp4"
        ext_c = os.path.splitext(video_c.filename)[1] or ".mp4"

        path_a = os.path.join(OUTPUT_DIR, f"{request_id}_a{ext_a}")
        path_c = os.path.join(OUTPUT_DIR, f"{request_id}_c{ext_c}")
    
        with open(path_a, "wb") as buffer:
            shutil.copyfileobj(video_a.file, buffer)
        with open(path_c, "wb") as buffer:
            shutil.copyfileobj(video_c.file, buffer)
        
        # This is blocking, so 'def' is required
        result = analyze_only(os.path.abspath(path_a), os.path.abspath(path_c), job_id=request_id)
    
        if result.get("status") == "error":
             raise HTTPException(status_code=500, detail=result.get("detail"))
         
        return {
            "prompt": result["prompt"],
            "video_a_path": os.path.abspath(path_a),
            "video_c_path": os.path.abspath(path_c)
        }
    except Exception as e:
        print(f"Server Error (Analyze): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate")
def generate_endpoint(
    background_tasks: BackgroundTasks,
    prompt: str = Body(...),
    video_a_path: str = Body(...),
    video_c_path: str = Body(...)
):
    # Changed to 'def' for safety
    try:
        if not os.path.exists(video_a_path) or not os.path.exists(video_c_path):
             raise HTTPException(status_code=400, detail="Video files not found on server.")

        job_id = str(uuid.uuid4())
        
        # Initialize job status
        status_file = os.path.join(OUTPUT_DIR, f"{job_id}.json")
        with open(status_file, "w") as f:
            json.dump({"status": "queued", "progress": 0, "log": "Job queued..."}, f)

        # Add to background tasks
        background_tasks.add_task(generate_only, prompt, video_a_path, video_c_path, job_id)
        
        return {"job_id": job_id}
        
    except Exception as e:
        print(f"Server Error (Generate): {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{job_id}")
def get_status(job_id: str):
    file_path = os.path.join(OUTPUT_DIR, f"{job_id}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Job not found")
        
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading status: {e}")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)
