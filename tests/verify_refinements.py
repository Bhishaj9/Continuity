import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os

# Import models and functions
from models import Base, User, Job, Transaction
from billing import reconcile_reservations, settle_transaction
from config import Settings

# Setup In-Memory DB for Tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def patch_session():
    with patch("billing.SessionLocal", side_effect=TestingSessionLocal):
        yield

def create_user_job_txn(db, job_status="generating", job_age_hours=0, txn_age_hours=2):
    user = User(username="test@example.com", balance=100)
    db.add(user)
    db.commit()

    # Created/Updated times
    now = datetime.utcnow()
    job_time = now - timedelta(hours=job_age_hours)
    txn_time = now - timedelta(hours=txn_age_hours)

    job = Job(id="job_1", user_id=user.id, status=job_status, created_at=job_time, updated_at=job_time)
    db.add(job)

    txn = Transaction(
        user_id=user.id,
        amount=-10,
        type='reserve',
        status='reserved',
        reference_id="job_1",
        created_at=txn_time
    )
    db.add(txn)
    db.commit()
    return user.id, "job_1", txn.id

def test_reconcile_active_fresh():
    # Old transaction (2h), Active Job (fresh, 0h) -> Should NOT refund
    db = TestingSessionLocal()
    user_id, job_id, txn_id = create_user_job_txn(db, job_status="stitching", job_age_hours=0, txn_age_hours=2)
    db.close()

    count = reconcile_reservations()
    assert count == 0

    db = TestingSessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    assert user.balance == 100 # No refund
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    assert txn.status == "reserved"
    db.close()

def test_reconcile_active_stale():
    # Old transaction (2h), Active Job (stale, 2h) -> Should refund
    db = TestingSessionLocal()
    user_id, job_id, txn_id = create_user_job_txn(db, job_status="stitching", job_age_hours=2, txn_age_hours=2)
    db.close()

    count = reconcile_reservations()
    assert count == 1

    db = TestingSessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    assert user.balance == 110 # Refunded 10
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    assert txn.status == "refunded"
    db.close()

def test_reconcile_failed():
    # Old transaction (2h), Failed Job -> Should refund
    db = TestingSessionLocal()
    user_id, job_id, txn_id = create_user_job_txn(db, job_status="error", job_age_hours=0.5, txn_age_hours=2)
    db.close()

    count = reconcile_reservations()
    assert count == 1

    db = TestingSessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    assert user.balance == 110
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    assert txn.status == "refunded"
    db.close()

def test_reconcile_completed():
    # Old transaction (2h), Completed Job -> Should NOT refund
    # (Note: Usually completed jobs settle transactions, but if it stuck in reserved, we check logic)
    db = TestingSessionLocal()
    user_id, job_id, txn_id = create_user_job_txn(db, job_status="completed", job_age_hours=0.5, txn_age_hours=2)
    db.close()

    count = reconcile_reservations()
    assert count == 0

    db = TestingSessionLocal()
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    assert txn.status == "reserved" # Should remain reserved if we strictly follow logic
    db.close()

def test_settle_transaction():
    db = TestingSessionLocal()
    create_user_job_txn(db, job_status="completed", job_age_hours=0, txn_age_hours=0)
    db.close()

    settle_transaction("job_1")

    db = TestingSessionLocal()
    txn = db.query(Transaction).filter(Transaction.reference_id == "job_1", Transaction.type == "reserve").first()
    assert txn.status == "settled"
    db.close()

def test_production_sqlite_check():
    # Mock os.getenv
    # We explicitly verify Settings.validate logic
    with patch.dict(os.environ, {"APP_ENV": "production", "DATABASE_URL": "sqlite:///test.db"}):
        with pytest.raises(EnvironmentError, match="Production Mode requires PostgreSQL"):
            Settings.validate()

def test_production_postgres_check():
    with patch.dict(os.environ, {"APP_ENV": "production", "DATABASE_URL": "postgresql://user:pass@localhost/db"}):
        try:
            Settings.validate()
        except EnvironmentError:
            pytest.fail("Should not raise EnvironmentError for PostgreSQL in production")
