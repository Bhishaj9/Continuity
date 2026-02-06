import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

# Add root directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure API Key is set
if "GOOGLE_API_KEY" not in os.environ:
    os.environ["GOOGLE_API_KEY"] = "dummy"

from server import app, get_db
from models import Base, User, Transaction
from billing import reconcile_reservations

# Setup In-Memory DB
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

# app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def override_dependency():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides = {}

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def patch_models_session():
    with patch("models.SessionLocal", side_effect=TestingSessionLocal), \
         patch("billing.SessionLocal", side_effect=TestingSessionLocal):
             yield

def test_reconciliation_logic():
    db = TestingSessionLocal()
    # Create user with balance
    user = User(username="stuck@example.com", balance=50)
    db.add(user)
    db.commit()

    # Create stuck reservation (> 1 hour old)
    old_time = datetime.utcnow() - timedelta(hours=2)
    txn = Transaction(
        user_id=user.id,
        amount=-20,
        type='reserve',
        status='reserved',
        created_at=old_time,
        reference_id="job_stuck"
    )
    db.add(txn)

    # Create fresh reservation (< 1 hour old)
    fresh_time = datetime.utcnow() - timedelta(minutes=30)
    txn2 = Transaction(
        user_id=user.id,
        amount=-10,
        type='reserve',
        status='reserved',
        created_at=fresh_time,
        reference_id="job_fresh"
    )
    db.add(txn2)
    db.commit()

    # Run reconciliation
    count = reconcile_reservations()

    assert count == 1

    # Verify user balance: 50 + 20 (refunded) = 70.
    db.refresh(user)
    assert user.balance == 70

    # Verify transaction statuses
    db.refresh(txn)
    db.refresh(txn2)
    assert txn.status == 'refunded'
    assert txn2.status == 'reserved'

    # Verify refund transaction created
    refund = db.query(Transaction).filter(
        Transaction.reference_id == "job_stuck",
        Transaction.type == 'refund'
    ).first()
    assert refund is not None
    assert refund.amount == 20
    db.close()

def test_reconciliation_endpoint():
    # Similar setup but call endpoint
    db = TestingSessionLocal()
    user = User(username="endpoint@example.com", balance=0)
    db.add(user)
    db.commit()

    old_time = datetime.utcnow() - timedelta(hours=2)
    txn = Transaction(
        user_id=user.id,
        amount=-10,
        type='reserve',
        status='reserved',
        created_at=old_time,
        reference_id="job_api"
    )
    db.add(txn)
    db.commit()
    db.close()

    response = client.post("/billing/reconcile", headers={"X-Admin-Key": "continuity_admin_secret"})
    assert response.status_code == 200
    assert response.json()["refunded_count"] == 1

def test_reconciliation_endpoint_unauthorized():
    response = client.post("/billing/reconcile", headers={"X-Admin-Key": "wrong"})
    assert response.status_code == 403

def test_jwt_issuer_enforcement():
    # Valid issuer
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "valid@example.com",
            "iss": "https://accounts.google.com"
        }
        response = client.get("/billing/balance", headers={"Authorization": "Bearer valid"})
        assert response.status_code == 200

    # Invalid issuer
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "hacker@example.com",
            "iss": "https://evil.com"
        }
        response = client.get("/billing/balance", headers={"Authorization": "Bearer invalid"})
        # Should be 401 or 403. Server code raises ValueError which is caught in get_current_user...
        # Wait, get_current_user catches ValueError and raises 401.
        assert response.status_code == 401
