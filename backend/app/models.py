from sqlalchemy import Column, String, Float, JSON, DateTime, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./certifake.db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class CertificateAnalysis(Base):
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, index=True)
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
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

Base.metadata.create_all(bind=engine)
