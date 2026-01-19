from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import os
import shutil
import uuid
import json
# FIXED IMPORT: Importing from root agent.py instead of continuity_agent
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
async def read_root():
    # Serve the dashboard HTML
    return FileResponse("stitch_continuity_dashboard/code.html")

@app.post("/analyze")
async def analyze_endpoint(
    background_tasks: BackgroundTasks,
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...)
):
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
        
        # Call Agent synchronously for analysis (it's relatively fast usually, but could be async too if desired)
        # For now keeping it sync as per user previous flows, but could be made async easily.
        # However, user request specifically focused on "generate" being async. 
        # But wait, analyze also calls Gemini which can be slow. 
        # Refactoring to also include job_id for analyze might be good practice but not explicitly requested for "analyze" in prompt detail, 
        # BUT the user request says: "Update analyze_videos node: Call utils.update_job_status...".
        # So we should probably treat analyze as async too OR just pass the job_id.
        # But the frontend flow for analyze currently awaits the response to get the prompt.
        # If we make it async, we break the frontend flow unless we refactor that too.
        # The prompt says: "Update code.html ... Update the generate button JavaScript." 
        # It doesn't explicitly say to update the analyze button logic to be async.
        # However, update_job_status IS called in analyze_videos.
        # So, we can pass a job_id if we want status updates, but if we await it, the status updates are only useful if polled in parallel.
        # For now, I will keep analyze synchronous but pass a dummy job_id if we want logging, or just let it block.
        # Actually, let's keep it blocking as per original server code, but pass a job_id so at least logs are written.
        
        # Call Agent with local paths
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
async def generate_endpoint(
    background_tasks: BackgroundTasks,
    prompt: str = Body(...),
    video_a_path: str = Body(...),
    video_c_path: str = Body(...)
):
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
async def get_status(job_id: str):
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
