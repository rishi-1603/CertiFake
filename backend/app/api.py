"""CertiFake Distributed API Gateway -- the single, canonical backend.

This is the ONE backend for CertiFake, replacing three previously
inconsistent entrypoints that existed side by side in this repository:
  1. `app/main.py` (a standalone synchronous demo with local JWT auth and
     inline forensics, storing users in a flat JSON file) -- now removed.
  2. This file, `app/api.py`, previously had NO authentication at all
     (commit "Remove authentication entirely for public access").
  3. `streamlit_app.py` at the repo root, a third parallel reimplementation
     of the forensic logic -- now removed; forensic logic lives in
     app/forensics.py, app/ocr.py, shared by this API and both workers.

Architecture (matches docker-compose.yml and k8s/deployment.yaml, which
already targeted `app.api:app` before this consolidation):

    Client
      |
      v
    POST /analyze (JWT required)  -----> S3/MinIO (raw file)
      |                                     |
      v                                     v
    Postgres (analysis row, status=analyzing)
      |
      v
    Kafka: "certificate_uploaded"
      |
      v
    worker-ocr  --(Kafka: ocr_completed)--> worker-forensics
      |                                          |
      v                                          v
    Postgres (ocr_text)                Postgres (score, verdict, status=completed)

    Client polls GET /status/{id} (JWT required, ownership-checked)

Security model: every analysis row is scoped to `owner_id` (the
authenticated user who uploaded it). GET /status/{id}, GET /report/{id}, and
GET /heatmap/{id} all verify the requester owns the analysis before
returning anything -- this did not exist even before the auth-removal
commit (the pre-removal code only checked "is there a valid token", not
"does this token's user own this specific analysis").
"""
import os
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import _get_db, login, register, require_user
from app.config import settings
from app.kafka_utils import get_kafka_producer, produce_event
from app.models import Base, CertificateAnalysis, User, engine
from app.rate_limit import check_rate_limit
from app.report import create_report
from app.s3_utils import StorageObjectNotFoundError, StorageUnavailableError, download_file_bytes, upload_file_bytes
from app.schemas import (
    AnalysisStatusResponse,
    AnalyzeAcceptedResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserRead,
)
from app.security import validate_upload

app = FastAPI(title="CertiFake Distributed API Gateway")

origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
producer = get_kafka_producer()


@app.get("/health")
def health():
    return {"status": "ok", "app": "CertiFake Distributed API Gateway"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/auth/register", response_model=TokenResponse, status_code=201)
def auth_register(payload: RegisterRequest, db: Session = Depends(_get_db)):
    user, token = register(db, payload.email, payload.password)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/auth/login", response_model=TokenResponse)
def auth_login(payload: LoginRequest, db: Session = Depends(_get_db)):
    user, token = login(db, payload.email, payload.password)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/auth/me", response_model=UserRead)
def auth_me(user: User = Depends(require_user)):
    return UserRead(id=user.id, email=user.email)


# ---------------------------------------------------------------------------
# Certificate analysis
# ---------------------------------------------------------------------------
def _get_owned_analysis_or_404(db: Session, analysis_id: str, user: User) -> CertificateAnalysis:
    """Fetch an analysis and verify the requesting user owns it.

    Returns 404 (not 403) for both "doesn't exist" and "exists but isn't
    yours" -- this deliberately avoids leaking which analysis IDs are valid
    to a user who doesn't own them (an information-disclosure concern, the
    same reasoning applied to auth.login's identical invalid-credentials
    message).
    """
    analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
    if not analysis or analysis.user_id != user.id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


def _rate_limit_analyze(user: User = Depends(require_user)) -> None:
    """Bounds how often one authenticated user can trigger /analyze --
    see app/rate_limit.py for why this endpoint specifically, and why the
    limiter fails open on a Redis outage. Scoped per-user (not per-IP):
    the endpoint already requires auth, so the user id is a stable,
    spoof-resistant key, unlike a client IP behind a shared NAT/proxy.
    """
    allowed, retry_after = check_rate_limit(
        f"analyze:{user.id}", limit=settings.analyze_rate_limit_per_minute, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many analysis requests. Please wait before uploading again.",
            headers={"Retry-After": str(retry_after)},
        )


@app.post("/analyze", response_model=AnalyzeAcceptedResponse, status_code=202)
async def analyze(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(_get_db),
    _rate_limited: None = Depends(_rate_limit_analyze),
):
    data = await file.read()
    # Real content validation (magic-byte sniffing, size limit, filename/path
    # sanitization) rather than trusting the client-supplied Content-Type
    # header, which is trivially spoofable. See app/security.py.
    content_type = validate_upload(filename=file.filename, data=data, declared_content_type=file.content_type)

    analysis_id = uuid.uuid4().hex
    safe_filename = os.path.basename(file.filename or "upload")
    file_key = f"{user.id}/{analysis_id}/{safe_filename}"
    try:
        upload_file_bytes(file_key, data)
    except StorageUnavailableError:
        # Fails BEFORE any DB row is created, so there is no orphaned
        # "analyzing" record left behind when storage is down -- unlike the
        # Kafka-publish-failure path below, which happens after the DB
        # commit and therefore explicitly marks the row "failed" instead.
        raise HTTPException(status_code=503, detail="File storage is temporarily unavailable. Please try again shortly.")

    new_analysis = CertificateAnalysis(
        id=analysis_id,
        user_id=user.id,
        filename=safe_filename,
        content_type=content_type,
        status="analyzing",
    )
    db.add(new_analysis)
    db.commit()

    event = {"analysis_id": analysis_id, "file_key": file_key, "content_type": content_type}
    delivered = produce_event(producer, "certificate_uploaded", analysis_id, event)

    if not delivered:
        # The file is safely in S3 and the DB row exists, but no worker will
        # ever pick it up without the Kafka event. Recording this explicitly
        # (rather than silently returning "analyzing" forever) means a
        # client polling GET /status/{id} sees "failed" with a clear reason
        # instead of waiting indefinitely for a completion that will never
        # come -- this is the failure-mode behavior this project's own
        # design docs call for (queue/broker outage handling), not just
        # "the API works when everything is working."
        new_analysis.status = "failed"
        db.add(new_analysis)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Certificate was stored but could not be queued for analysis (message broker unavailable). Please try again shortly.",
        )

    return {"analysis_id": analysis_id, "status": "analyzing", "message": "Certificate queued for distributed analysis"}


@app.get("/status/{analysis_id}", response_model=AnalysisStatusResponse)
def get_status(analysis_id: str, user: User = Depends(require_user), db: Session = Depends(_get_db)):
    analysis = _get_owned_analysis_or_404(db, analysis_id, user)
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
def get_heatmap(analysis_id: str, user: User = Depends(require_user), db: Session = Depends(_get_db)):
    _get_owned_analysis_or_404(db, analysis_id, user)
    heatmap_key = f"{user.id}/{analysis_id}/heatmap.png"
    try:
        file_bytes = download_file_bytes(heatmap_key)
    except StorageObjectNotFoundError:
        raise HTTPException(status_code=404, detail="Heatmap not found")
    except StorageUnavailableError:
        raise HTTPException(status_code=503, detail="File storage is temporarily unavailable. Please try again shortly.")
    return Response(content=file_bytes, media_type="image/png")


@app.get("/report/{analysis_id}")
def get_report(analysis_id: str, user: User = Depends(require_user), db: Session = Depends(_get_db)):
    analysis = _get_owned_analysis_or_404(db, analysis_id, user)
    if analysis.status != "completed":
        raise HTTPException(status_code=404, detail="Report not ready or found")

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        report_path = tmp.name

    create_report(
        report_path,
        {
            "file_name": analysis.filename,
            "user": user.email,
            "authenticity_score": analysis.authenticity_score or 0,
            "verdict": analysis.verdict or "Unknown",
            "signals": ", ".join(analysis.suspicious_signals or []) or "None",
            "ocr_preview": (analysis.ocr_text or "")[:1500],
            "fields": analysis.extracted_fields or {},
            "python_compat": "3.11",
        },
    )

    with open(report_path, "rb") as f:
        pdf_bytes = f.read()
    os.remove(report_path)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=CertiFake_Report_{analysis_id}.pdf"},
    )
