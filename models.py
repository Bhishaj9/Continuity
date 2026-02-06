from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./continuity.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    balance = Column(Integer, default=0)
    stripe_customer_id = Column(String, nullable=True)

    jobs = relationship("Job", back_populates="owner")
    transactions = relationship("Transaction", back_populates="user")

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Integer)
    type = Column(String) # 'purchase', 'reserve', 'settle', 'refund'
    status = Column(String, default="completed") # 'completed', 'reserved', 'settled', 'refunded'
    reference_id = Column(String, nullable=True) # job_id or session_id
    stripe_event_id = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="transactions")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String)
    progress = Column(Integer)
    log = Column(String)
    video_url = Column(String, nullable=True)
    merged_video_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=1)

    __mapper_args__ = {
        "version_id_col": version
    }

    __table_args__ = (
        Index('idx_user_created', "user_id", created_at.desc()),
        Index('idx_jobs_status_completed', "status", postgresql_where=(status == 'completed')),
    )

    owner = relationship("User", back_populates="jobs")

def init_db():
    Base.metadata.create_all(bind=engine)
