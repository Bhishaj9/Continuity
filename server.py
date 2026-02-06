# ------------------------------------------------------------------------------
# Continuity - AI Video Bridging Service
# Copyright (c) 2026 Bhishaj. All Rights Reserved.
#
# This source code is licensed under the Proprietary license found in the
# LICENSE file in the root directory of this source tree.
# ------------------------------------------------------------------------------
from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Body, Depends, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
import uvicorn, os, shutil, uuid, asyncio
from agent import analyze_only, generate_only
from utils import get_history_from_gcs
from models import SessionLocal, User, Job, init_db

app = FastAPI(title="Continuity", description="AI Video Bridging Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

# Initialize Database
init_db()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class JobQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_processing = False

    async def add_job(self, job_func, *args):
        await self.queue.put((job_func, args))
        if not self.is_processing:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        self.is_processing = True
        while not self.queue.empty():
            func, args = await self.queue.get()
            try:
                await asyncio.to_thread(func, *args)
            except Exception as e:
                print(f"Queue Error: {e}")
            self.queue.task_done()
        self.is_processing = False

job_queue = JobQueue()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    # TODO: In a production environment, this token should be verified against
    # Google's auth servers or decoded if it's a JWT.
    # For this foundation stage, we only check that a token is present.
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.username == token).first()
    if not user:
        user = User(username=token)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

@app.get("/")
def read_root():
    return FileResponse("stitch_continuity_dashboard/code.html")

@app.post("/analyze")
def analyze_endpoint(
    video_a: UploadFile = File(...),
    video_c: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        rid = str(uuid.uuid4())

        # Create Job in DB
        job = Job(id=rid, user_id=user.id, status="analyzing", progress=0, log="Analysis started...")
        db.add(job)
        db.commit()

        pa = os.path.join("outputs", f"{rid}_a.mp4")
        pc = os.path.join("outputs", f"{rid}_c.mp4")
        
        with open(pa, "wb") as b:
            shutil.copyfileobj(video_a.file, b)
        with open(pc, "wb") as b:
            shutil.copyfileobj(video_c.file, b)
            
        res = analyze_only(os.path.abspath(pa), os.path.abspath(pc), job_id=rid)
        
        if res.get("status") == "error":
            raise HTTPException(500, res.get("detail"))
            
        return {
            "analysis_a": res.get("analysis_a"),
            "analysis_c": res.get("analysis_c"),
            "prompt": res["prompt"],
            "video_a_path": os.path.abspath(pa),
            "video_c_path": os.path.abspath(pc)
        }
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/generate")
async def generate_endpoint(
    prompt: str = Body(...),
    style: str = Body("Cinematic"),
    audio_prompt: str = Body("Cinematic"),
    negative_prompt: str = Body(""),
    guidance_scale: float = Body(5.0),
    motion_strength: int = Body(5),
    video_a_path: str = Body(...),
    video_c_path: str = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not os.path.exists(video_a_path) or not os.path.exists(video_c_path):
        raise HTTPException(400, "Videos not found.")
        
    job_id = str(uuid.uuid4())

    # Create Job in DB (Async wrapper)
    def create_job_record():
        job = Job(id=job_id, user_id=user.id, status="queued", progress=0, log="Queued...")
        db.add(job)
        db.commit()

    await run_in_threadpool(create_job_record)
        
    await job_queue.add_job(generate_only, prompt, video_a_path, video_c_path, job_id, style, audio_prompt, negative_prompt, guidance_scale, motion_strength)
    return {"job_id": job_id}

@app.get("/status/{job_id}")
def get_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    return {
        "status": job.status,
        "progress": job.progress,
        "log": job.log,
        "video_url": job.video_url,
        "merged_video_url": job.merged_video_url
    }

@app.get("/history")
def get_history():
    return get_history_from_gcs()

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)
