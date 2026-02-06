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
from models import Base, User, Job, Transaction
from agent import analyze_only, generate_only
from google.oauth2 import id_token

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

# app.dependency_overrides[get_db] = override_get_db # Moved to fixture

client = TestClient(app)

@pytest.fixture(autouse=True)
def override_dependency():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides = {}

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

@pytest.fixture
def mock_verify_token():
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock:
        mock.return_value = {
            "email": "test@example.com",
            "sub": "12345",
            "iss": "https://accounts.google.com"
        }
        yield mock

@pytest.fixture
def mock_stripe():
    with patch("billing.stripe") as mock:
        mock.checkout.Session.create.return_value = MagicMock(url="http://checkout.url")
        mock.Webhook.construct_event.return_value = {
            'id': 'evt_mock',
            'type': 'checkout.session.completed',
            'data': {'object': {'client_reference_id': '1', 'amount_total': 1000, 'id': 'sess_123', 'customer': 'cus_123'}}
        }
        yield mock

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

# Patch SessionLocal in models, utils AND billing to use our TestingSessionLocal
@pytest.fixture(autouse=True)
def patch_models_session():
    with patch("models.SessionLocal", side_effect=TestingSessionLocal), \
         patch("utils.SessionLocal", side_effect=TestingSessionLocal), \
         patch("billing.SessionLocal", side_effect=TestingSessionLocal):
             yield

@pytest.fixture(autouse=True)
def mock_settings():
    with patch("billing.Settings.STRIPE_SECRET_KEY", "sk_test_mock"), \
         patch("billing.Settings.STRIPE_WEBHOOK_SECRET", "whsec_mock"):
        yield

# --- Server Tests ---

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200

@patch("server.analyze_only")
def test_analyze_endpoint(mock_analyze, mock_verify_token):
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
    user = db.query(User).filter(User.username == "test@example.com").first()
    assert user is not None
    job = db.query(Job).filter(Job.user_id == user.id).first()
    assert job is not None
    assert job.status == "analyzing"
    db.close()

@patch("server.job_queue.add_job")
def test_generate_endpoint(mock_add_job, mock_verify_token):
    # Give user credits first
    db = TestingSessionLocal()
    user = User(username="test@example.com", balance=100)
    db.add(user)
    db.commit()
    db.close()

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
         assert job.owner.username == "test@example.com"
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

def test_invalid_token_verification():
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("Token invalid")):
        files = {
            "video_a": ("video_a.mp4", MOCK_VIDEO_CONTENT, "video/mp4"),
            "video_c": ("video_c.mp4", MOCK_VIDEO_CONTENT, "video/mp4")
        }
        # Sending a token that will fail verification
        response = client.post("/analyze", files=files, headers={"Authorization": "Bearer invalidtoken"})
        assert response.status_code == 401
        assert "Invalid authentication credentials" in response.json()["detail"]

def test_billing_checkout(mock_verify_token, mock_stripe):
    # Ensure user exists (mock_verify_token returns test@example.com)
    # We rely on get_current_user to create it if not exists, but we mock it.
    # Actually get_current_user uses DB.
    # We need to ensure DB has user or it's created.
    # In integration test with client, get_current_user runs.
    response = client.post("/billing/checkout", json={"quantity": 10}, headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["url"] == "http://checkout.url"

def test_billing_balance(mock_verify_token):
    # Setup user with balance.
    # Note: get_current_user will find this user by email from mock_verify_token
    db = TestingSessionLocal()
    user = User(username="test@example.com", balance=50)
    db.add(user)
    db.commit()
    db.close()

    response = client.get("/billing/balance", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["balance"] == 50

def test_stripe_webhook(mock_stripe):
    # Create user
    db = TestingSessionLocal()
    user = User(username="webhook@example.com", id=1, balance=0)
    db.add(user)
    db.commit()
    db.close()

    # Call webhook
    response = client.post("/webhook/stripe", json={}, headers={"stripe-signature": "sig"})
    assert response.status_code == 200

    # Verify balance update
    db = TestingSessionLocal()
    user = db.query(User).filter(User.id == 1).first()
    assert user.balance == 10 # 1000 cents / 100
    txn = db.query(Transaction).filter(Transaction.user_id == 1).first()
    assert txn.type == "purchase"
    assert txn.amount == 10
    db.close()

@patch("server.job_queue.add_job")
def test_generate_insufficient_funds(mock_add_job, mock_verify_token):
    # User has 0 balance (default)
    with patch("os.path.exists", return_value=True):
         payload = {
             "prompt": "Test prompt",
             "video_a_path": "outputs/a.mp4",
             "video_c_path": "outputs/c.mp4"
         }
         # Now returns 200 because check is async
         response = client.post("/generate", json=payload, headers={"Authorization": "Bearer token"})

         assert response.status_code == 200
         mock_add_job.assert_called_once()

@patch("server.job_queue.add_job")
def test_generate_reserve_success(mock_add_job, mock_verify_token):
    # Setup user with balance
    db = TestingSessionLocal()
    user = db.query(User).filter(User.username == "test@example.com").first()
    if not user:
        user = User(username="test@example.com", balance=20)
        db.add(user)
    else:
        user.balance = 20
    db.commit()
    db.close()

    with patch("os.path.exists", return_value=True):
         payload = {
             "prompt": "Test prompt",
             "video_a_path": "outputs/a.mp4",
             "video_c_path": "outputs/c.mp4"
         }
         response = client.post("/generate", json=payload, headers={"Authorization": "Bearer token"})

         assert response.status_code == 200
         mock_add_job.assert_called_once()

         # Check balance NOT deducted yet (async)
         db = TestingSessionLocal()
         user = db.query(User).filter(User.username == "test@example.com").first()
         assert user.balance == 20
         db.close()

def test_generate_refund_on_failure(mock_genai_client):
    # Setup DB with user and job
    db = TestingSessionLocal()
    user = User(username="fail@example.com", balance=100) # Give enough balance for reservation
    db.add(user)
    db.commit()
    job = Job(id="fail_job", user_id=user.id, status="generating")
    db.add(job)
    db.commit()

    # Get ID before closing session to avoid DetachedInstanceError
    user_id = user.id
    db.close()

    # Mock GenAI to raise exception
    mock_genai_client.return_value.models.generate_videos.side_effect = Exception("Veo Error")

    with patch("agent.Settings.GCP_PROJECT_ID", "dummy"):
        # Pass user_id
        generate_only("prompt", "a", "c", "fail_job", "style", "audio", "neg", 5.0, 5, user_id)

    # Check refund: Balance should be 100 - 10 (reserve) + 10 (refund) = 100
    db = TestingSessionLocal()
    user = db.query(User).filter(User.username == "fail@example.com").first()
    assert user.balance == 100

    # Check transactions: 1 reserve, 1 refund
    reserve = db.query(Transaction).filter(Transaction.user_id == user.id, Transaction.type == "reserve").first()
    refund = db.query(Transaction).filter(Transaction.user_id == user.id, Transaction.type == "refund").first()
    assert reserve is not None
    assert refund is not None
    assert reserve.status == "refunded"
    assert refund.status == "settled"
    db.close()

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
    # Setup User for reservation
    db = TestingSessionLocal()
    user = User(username="worker@test.com", balance=100)
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

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
                         motion=5,
                         user_id=user_id
                     )

                     # Verify final status update
                     mock_update_status.assert_called_with(
                         "job_123", "completed", 100, "Done! (Merged)",
                         video_url=ANY, merged_video_url="outputs/merged.mp4"
                     )

                     # Verify settled
                     db = TestingSessionLocal()
                     txn = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.type == "reserve").first()
                     assert txn.status == "settled"
                     db.close()
