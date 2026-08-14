from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uuid
import os
import io
from fastapi.responses import Response, StreamingResponse
from app.report import create_report

from app.models import SessionLocal, CertificateAnalysis, Base, engine
from app.kafka_utils import get_kafka_producer, produce_event
from app.s3_utils import upload_file_bytes

app = FastAPI(title="CertiFake Distributed API Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
producer = get_kafka_producer()

@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Unsupported file type")
        
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    analysis_id = uuid.uuid4().hex
    
    # Upload to Minio (S3)
    file_key = f"{analysis_id}/{file.filename}"
    upload_file_bytes(file_key, data)
    
    # Create record in DB
    db = SessionLocal()
    new_analysis = CertificateAnalysis(
        id=analysis_id,
        filename=file.filename,
        content_type=file.content_type,
        status="analyzing"
    )
    db.add(new_analysis)
    db.commit()
    db.close()
    
    # Emit Kafka Event
    event = {
        "analysis_id": analysis_id,
        "file_key": file_key,
        "content_type": file.content_type
    }
    produce_event(producer, "certificate_uploaded", analysis_id, event)
    
    return {"analysis_id": analysis_id, "status": "analyzing", "message": "Certificate queued for distributed analysis"}

@app.get("/status/{analysis_id}")
def get_status(analysis_id: str):
    db = SessionLocal()
    analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
    db.close()
    
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
        
    return {
        "analysis_id": analysis.id,
        "status": analysis.status,
        "authenticity_score": analysis.authenticity_score,
        "verdict": analysis.verdict,
        "ocr_text": analysis.ocr_text,
        "extracted_fields": analysis.extracted_fields,
        "suspicious_signals": analysis.suspicious_signals,
        "confidence": analysis.confidence,
    }

@app.get("/heatmap/{analysis_id}")
def get_heatmap(analysis_id: str):
    from app.s3_utils import download_file_bytes
    heatmap_key = f"{analysis_id}/heatmap.png"
    try:
        file_bytes = download_file_bytes(heatmap_key)
        return Response(content=file_bytes, media_type="image/png")
    except Exception:
        raise HTTPException(status_code=404, detail="Heatmap not found")

@app.get("/report/{analysis_id}")
def get_report(analysis_id: str):
    db = SessionLocal()
    analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
    db.close()
    
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="Report not ready or found")
        
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        report_path = tmp.name
        
    create_report(report_path, {
        "file_name": analysis.filename,
        "user": "guest",
        "authenticity_score": analysis.authenticity_score or 0,
        "verdict": analysis.verdict or "Unknown",
        "signals": ", ".join(analysis.suspicious_signals or []) or "None",
        "ocr_preview": (analysis.ocr_text or "")[:1500],
        "fields": analysis.extracted_fields or {},
        "python_compat": "3.11",
    })
    
    with open(report_path, "rb") as f:
        pdf_bytes = f.read()
    os.remove(report_path)
    
    return Response(content=pdf_bytes, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=CertiFake_Report_{analysis_id}.pdf"
    })

