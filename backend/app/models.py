from sqlalchemy import Column, String, Float, JSON, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone
import os


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./certifake.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    analyses = relationship("CertificateAnalysis", back_populates="owner")


class CertificateAnalysis(Base):
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, index=True)
    # Nullable ONLY to keep the schema valid for rows created before auth was
    # reinstated; every new row created through the API always sets this via
    # the authenticated user's id. See app/api.py:analyze() and
    # app/auth.py:require_user() for how ownership is enforced.
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    filename = Column(String)
    content_type = Column(String)
    status = Column(String, default="pending")  # pending, analyzing, completed, failed

    # OCR Results
    ocr_text = Column(String, nullable=True)
    extracted_fields = Column(JSON, nullable=True)

    # Forensic Results
    authenticity_score = Column(Float, nullable=True)
    verdict = Column(String, nullable=True)
    suspicious_signals = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)

    # Output
    report_url = Column(String, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    owner = relationship("User", back_populates="analyses")


Base.metadata.create_all(bind=engine)
