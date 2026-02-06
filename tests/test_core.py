import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch, mock_open, ANY
from fastapi.testclient import TestClient

# Add root directory to sys.path to allow importing server and agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure API Key is set for Config
if "GOOGLE_API_KEY" not in os.environ:
    os.environ["GOOGLE_API_KEY"] = "dummy"

# Import server
from server import app
from agent import analyze_only, generate_only

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

    # Mock open and shutil.copyfileobj in server.py
    with patch("builtins.open", mock_open()), \
         patch("shutil.copyfileobj"):

        response = client.post("/analyze", files=files, headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == 200
    data = response.json()
    assert data["analysis_a"] == "desc A"
    assert data["prompt"] == "prompt B"

@patch("server.job_queue.add_job")
def test_generate_endpoint(mock_add_job):
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open()):

             payload = {
                 "prompt": "Test prompt",
                 "video_a_path": "outputs/a.mp4",
                 "video_c_path": "outputs/c.mp4"
             }
             response = client.post("/generate", json=payload, headers={"Authorization": "Bearer testtoken"})

             assert response.status_code == 200
             assert "job_id" in response.json()
             mock_add_job.assert_called_once()

def test_get_status():
    job_id = "test_job_123"
    mock_status = {"status": "processing", "progress": 50}

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_status))):
            response = client.get(f"/status/{job_id}")
            assert response.status_code == 200
            assert response.json() == mock_status

def test_get_status_not_found():
    with patch("os.path.exists", return_value=False):
        response = client.get("/status/nonexistent")
        assert response.status_code == 404

def test_license_compliance():
    assert os.path.exists("LICENSE"), "LICENSE file missing"
    with open("LICENSE", "r") as f:
        content = f.read()
    # Check for 'Proprietary' (case-insensitive as found in file: "proprietary")
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
    # Ensure status updates were called
    assert mock_update_status.call_count >= 1

def test_generate_only(mock_genai_client, mock_stitch, mock_update_status, mock_sleep):
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
