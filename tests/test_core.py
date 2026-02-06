import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch, mock_open, ANY
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# Add root directory to sys.path to allow importing server and agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure API Key is set for Config
if "GOOGLE_API_KEY" not in os.environ:
    os.environ["GOOGLE_API_KEY"] = "dummy"

# Import server
from server import app, get_db
from models import Base, User, Job
from agent import analyze_only, generate_only

# Setup In-Memory DB for Tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

MOCK_VIDEO_CONTENT = b"fake video content"
MOCK_ANALYSIS_RESPONSE = {
    "analysis_a": "A video",
    "analysis_c": "C video",
    "visual_prompt_b": "Morph A to C"
}

@pytest.fixture
def mock_genai_client():
    with patch("agent.genai.Client") as mock:
        yield mock

@pytest.fixture
def mock_stitch():
    with patch("agent.stitch_videos") as mock:
        mock.return_value = "outputs/merged.mp4"
        yield mock

@pytest.fixture
def mock_update_status():
    with patch("agent.update_job_status") as mock:
        yield mock

@pytest.fixture
def mock_sleep():
    with patch("time.sleep") as mock:
        yield mock

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

# Patch SessionLocal in models and utils to use our TestingSessionLocal
@pytest.fixture(autouse=True)
def patch_models_session():
    with patch("models.SessionLocal", side_effect=TestingSessionLocal), \
         patch("utils.SessionLocal", side_effect=TestingSessionLocal):
             yield

# --- Server Tests ---

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200

@patch("server.analyze_only")
def test_analyze_endpoint(mock_analyze):
    mock_analyze.return_value = {
        "analysis_a": "desc A",
        "analysis_c": "desc C",
        "prompt": "prompt B",
        "status": "success"
    }

    files = {
        "video_a": ("video_a.mp4", MOCK_VIDEO_CONTENT, "video/mp4"),
        "video_c": ("video_c.mp4", MOCK_VIDEO_CONTENT, "video/mp4")
    }

    # Mock shutil.copyfileobj in server.py and builtins.open
    with patch("shutil.copyfileobj"), \
         patch("builtins.open", mock_open()):

        response = client.post("/analyze", files=files, headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == 200
    data = response.json()
    assert data["analysis_a"] == "desc A"

    # Verify User and Job creation
    db = TestingSessionLocal()
    user = db.query(User).filter(User.username == "testtoken").first()
    assert user is not None
    job = db.query(Job).filter(Job.user_id == user.id).first()
    assert job is not None
    assert job.status == "analyzing"
    db.close()

@patch("server.job_queue.add_job")
def test_generate_endpoint(mock_add_job):
    with patch("os.path.exists", return_value=True):
         payload = {
             "prompt": "Test prompt",
             "video_a_path": "outputs/a.mp4",
             "video_c_path": "outputs/c.mp4"
         }
         response = client.post("/generate", json=payload, headers={"Authorization": "Bearer testtoken"})

         assert response.status_code == 200
         data = response.json()
         assert "job_id" in data
         mock_add_job.assert_called_once()

         # Verify DB
         db = TestingSessionLocal()
         job = db.query(Job).filter(Job.id == data["job_id"]).first()
         assert job is not None
         assert job.status == "queued"
         assert job.owner.username == "testtoken"
         db.close()

def test_get_status():
    # Setup data in DB
    db = TestingSessionLocal()
    job = Job(id="test_job_123", status="processing", progress=50, log="log")
    db.add(job)
    db.commit()
    db.close()

    response = client.get(f"/status/test_job_123")
    assert response.status_code == 200
    assert response.json()["status"] == "processing"

def test_get_status_not_found():
    response = client.get("/status/nonexistent")
    assert response.status_code == 404

def test_license_compliance():
    assert os.path.exists("LICENSE"), "LICENSE file missing"
    with open("LICENSE", "r") as f:
        content = f.read()
    assert "Proprietary" in content or "proprietary" in content, "LICENSE must contain 'Proprietary'"

def test_unauthorized_access():
    files = {
        "video_a": ("video_a.mp4", MOCK_VIDEO_CONTENT, "video/mp4"),
        "video_c": ("video_c.mp4", MOCK_VIDEO_CONTENT, "video/mp4")
    }
    # No Auth Header
    response = client.post("/analyze", files=files)
    assert response.status_code == 401

    payload = {"prompt": "test", "video_a_path": "a", "video_c_path": "c"}
    response = client.post("/generate", json=payload)
    assert response.status_code == 401

# --- Agent Tests ---

def test_analyze_only(mock_genai_client, mock_update_status, mock_sleep):
    client_instance = mock_genai_client.return_value

    # Mock file upload
    mock_file = MagicMock()
    mock_file.state.name = "ACTIVE"
    mock_file.name = "file_name"
    client_instance.files.upload.return_value = mock_file
    client_instance.files.list.return_value = []
    client_instance.files.get.return_value = mock_file

    # Mock generate_content
    mock_response = MagicMock()
    mock_response.text = json.dumps([MOCK_ANALYSIS_RESPONSE])
    client_instance.models.generate_content.return_value = mock_response

    with patch("agent.get_file_hash", return_value="dummy_hash"):
        result = analyze_only("path/a.mp4", "path/c.mp4", job_id="test_id")

    assert result["status"] == "success"
    assert result["analysis_a"] == "A video"
    assert mock_update_status.call_count >= 1

def test_generate_only(mock_genai_client, mock_stitch, mock_update_status, mock_sleep):
    # Patch get_job_from_db to return a completed status so safety net doesn't trigger error update
    with patch("agent.get_job_from_db", return_value={"status": "completed"}):
        client_instance = mock_genai_client.return_value

        # Mock generate_videos
        mock_op = MagicMock()
        mock_op.name = "operation_name"
        client_instance.models.generate_videos.return_value = mock_op

        # Mock operations.get (polling)
        mock_refreshed_op = MagicMock()
        mock_refreshed_op.done = True

        # Mock result
        mock_video_result = MagicMock()
        mock_video_result.generated_videos = [MagicMock()]
        mock_video_result.generated_videos[0].video.uri = "gs://bucket/video.mp4"

        mock_refreshed_op.result = mock_video_result
        client_instance.operations.get.return_value = mock_refreshed_op

        with patch("agent.download_blob"), \
             patch("agent.Settings.GCP_PROJECT_ID", "dummy_project"), \
             patch("tempfile.mktemp", return_value="temp.mp4"):

                     generate_only(
                         prompt="test prompt",
                         path_a="a.mp4",
                         path_c="c.mp4",
                         job_id="job_123",
                         style="Cinematic",
                         audio="Soundtrack",
                         neg="blur",
                         guidance=5.0,
                         motion=5
                     )

                     # Verify final status update
                     mock_update_status.assert_called_with(
                         "job_123", "completed", 100, "Done! (Merged)",
                         video_url=ANY, merged_video_url="outputs/merged.mp4"
                     )
