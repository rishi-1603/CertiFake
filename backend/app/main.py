from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import os, uuid

from app.config import settings
from app.auth import login, require_user
from app.schemas import LoginRequest, TokenResponse, AnalyzeResponse
from app.utils import allowed_mime, save_path, ensure_dirs
from app.ocr import run_ocr, extract_fields
from app.forensics import score_document, heatmap_b64, CV_AVAILABLE
from app.report import create_report
from app.storage import init_storage

app = FastAPI(title=settings.app_name)

origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REPORTS_DIR = Path(settings.reports_dir)

@app.on_event("startup")
def startup():
    init_storage()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "python_compat": "3.14",
        "opencv_available": CV_AVAILABLE
    }

@app.post("/auth/login", response_model=TokenResponse)
def auth_login(payload: LoginRequest):
    token = login(payload.username, payload.password)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/signup", response_model=TokenResponse)
def auth_signup(payload: LoginRequest):
    from app.auth import signup
    token = signup(payload.username, payload.password)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(file: UploadFile = File(...), user=Depends(require_user)):
    if not allowed_mime(file.content_type):
        raise HTTPException(status_code=400, detail="Unsupported file type")
        
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
        
    ext = os.path.splitext(file.filename or "")[1].lower()
    if file.content_type == "application/pdf" and ext not in [".pdf", ""]:
        raise HTTPException(status_code=400, detail="Invalid PDF file")
        
    if file.content_type.startswith("image/") and ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(status_code=400, detail="Invalid image extension")
        
    path = save_path(file.filename or ("upload.pdf" if file.content_type == "application/pdf" else "upload.jpg"))
    with open(path, "wb") as f:
        f.write(data)
        
    try:
        ocr_text = run_ocr(path, file.content_type)
        extracted = extract_fields(ocr_text)
        score, signals, gray = score_document(path, ocr_text, file.content_type)
        heat = heatmap_b64(gray)
        
        if score >= 80:
            verdict = "Likely Genuine"
        elif score >= 55:
            verdict = "Needs Review"
        else:
            verdict = "Likely Fake"
            
        confidence = round(score / 100.0, 2)
        report_id = uuid.uuid4().hex
        report_path = REPORTS_DIR / f"{report_id}.pdf"
        
        create_report(str(report_path), {
            "file_name": file.filename,
            "user": user,
            "authenticity_score": score,
            "verdict": verdict,
            "signals": ", ".join(signals) or "None",
            "ocr_preview": ocr_text[:1500],
            "fields": extracted,
            "python_compat": "3.14",
        })
        
        return {
            "file_name": file.filename,
            "content_type": file.content_type,
            "authenticity_score": round(score, 2),
            "verdict": verdict,
            "ocr_text": ocr_text[:4000],
            "extracted_fields": extracted,
            "suspicious_signals": signals,
            "confidence": confidence,
            "report_id": report_id,
            "preview_path": f"data:image/png;base64,{heat}" if heat else None,
        }
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

@app.get("/report/{report_id}")
def get_report(report_id: str, user=Depends(require_user)):
    report_path = REPORTS_DIR / f"{report_id}.pdf"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(str(report_path), media_type="application/pdf", filename=f"CertiFake_Report_{report_id}.pdf")
