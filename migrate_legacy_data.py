import os
import json
import glob
from datetime import datetime
from models import SessionLocal, Job, init_db

def migrate():
    print("Starting migration of legacy JSON files...")
    # Ensure DB is initialized
    init_db()

    db = SessionLocal()

    # Find all JSON files in outputs/
    files = glob.glob(os.path.join("outputs", "*.json"))
    count = 0

    for filepath in files:
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                print(f"Skipping {filepath}: Content is not a dictionary.")
                continue

            # Infer Job ID
            job_id = data.get("id")
            if not job_id:
                # Try to get from filename
                filename = os.path.basename(filepath)
                name_part = os.path.splitext(filename)[0]
                # Some files might be named {id}.json
                job_id = name_part

            # Check if exists
            existing = db.query(Job).filter(Job.id == job_id).first()
            if existing:
                print(f"Skipping {job_id} (already in DB)")
                continue

            print(f"Importing {job_id} from {filepath}...")

            # Create job record
            job = Job(
                id=job_id,
                status=data.get("status", "completed"),
                progress=data.get("progress", 100),
                log=data.get("log", "Imported from legacy JSON"),
                video_url=data.get("video_url"),
                merged_video_url=data.get("merged_video_url"),
                user_id=data.get("user_id"), # Might be None
                version=1
            )

            # Try to preserve created_at from file modification time if not in data
            # (ignoring complex date parsing for data['created_at'] to avoid errors)
            mtime = os.path.getmtime(filepath)
            job.created_at = datetime.fromtimestamp(mtime)

            db.add(job)
            count += 1

        except Exception as e:
            print(f"Failed to migrate {filepath}: {e}")

    try:
        db.commit()
        print(f"Migration complete. Imported {count} jobs.")
    except Exception as e:
        print(f"Migration commit failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
