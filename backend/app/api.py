"""FastAPI gateway for authenticated certificate analysis.

Uploads write the object to S3, then commit an analysis and outbox event in
one database transaction. The OCR worker also runs the outbox dispatcher;
202 means queued durably in PostgreSQL, not completed or broker-delivered.
See docs/DISTRIBUTED_PROCESSING.md for cross-service failure windows.
"""
import io
import os
import time
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import _get_db, login, register, require_user
from app.config import settings
from app.metrics import analyze_request_duration_seconds, analyze_requests_total
from app.models import CertificateAnalysis, OutboxEvent, User
from app.rate_limit import check_rate_limit
from app.report import create_report
from app.s3_utils import (
    StorageObjectNotFoundError,
    StorageUnavailableError,
    download_file_bytes,
    upload_file_bytes,
)
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

# No create_all() call here, and no Base/engine import for one: importing
# app.models above already ran init_db(), which takes a Postgres advisory lock
# so api-gateway, worker-ocr and worker-forensics cannot race on CREATE TABLE
# when all three boot against one fresh database. A second bare create_all()
# here used to do exactly that -- it duplicated the work and bypassed the lock.
# See app/models.py:init_db for the full reasoning.

@app.get("/health")
def health():
    return {"status": "ok", "app": "CertiFake Distributed API Gateway"}


@app.get("/metrics")
def metrics():
    """Prometheus scrape endpoint -- see app/metrics.py for what is
    actually tracked and why. This closes a real gap: monitoring/
    prometheus.yml already targeted this exact path (on api-gateway,
    worker-ocr, and worker-forensics) before any of the three exposed it.

    Deliberately a plain route (not `app.mount(..., make_asgi_app())`):
    Starlette's Mount treats "/metrics" as a routing prefix and 307-
    redirects a bare `GET /metrics` (no trailing slash) to `/metrics/` --
    harmless for well-behaved HTTP clients that follow redirects, but an
    unnecessary trap for the exact default path a Prometheus scrape
    config or a quick `curl` would use. A plain route serves the same
    content at the exact path with no redirect involved. Unauthenticated
    like /health -- Prometheus has no way to send this API's JWTs.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
        analyze_requests_total.labels(outcome="rate_limited").inc()
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
    # Only the request portion is timed (upload validation,
    # S3 write and database/outbox commit) -- OCR/forensics happen later in
    # separate worker processes and are timed/counted there instead (see
    # app/workers/ocr.py, app/workers/forensics.py, app/metrics.py).
    start = time.monotonic()
    try:
        # Multipart parsing may already spool the body; a proxy-level request
        # cap is still needed. This bounds how much this handler reads in memory.
        limit = settings.max_upload_mb * 1024 * 1024
        chunks = []
        size = 0
        while True:
            chunk = await file.read(min(1024 * 1024, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"File too large (max {settings.max_upload_mb}MB).")
            chunks.append(chunk)
        data = b"".join(chunks)
        # Real content validation (magic-byte sniffing, size limit, filename/path
        # sanitization) rather than trusting the client-supplied Content-Type
        # header, which is trivially spoofable. See app/security.py.
        content_type = validate_upload(filename=file.filename, data=data, declared_content_type=file.content_type)

        analysis_id = uuid.uuid4().hex
        safe_filename = os.path.basename((file.filename or "upload").replace("\\", "/"))
        safe_filename = "".join(c for c in safe_filename if c.isprintable())[:200] or "upload"
        extension = os.path.splitext(safe_filename)[1].lower()
        file_key = f"{user.id}/{analysis_id}/original{extension}"
        try:
            await run_in_threadpool(upload_file_bytes, file_key, data)
        except StorageUnavailableError:
            # No analysis or outbox row exists when the storage write fails.
            analyze_requests_total.labels(outcome="storage_unavailable").inc()
            raise HTTPException(
                status_code=503, detail="File storage is temporarily unavailable. Please try again shortly."
            )

        new_analysis = CertificateAnalysis(
            id=analysis_id,
            user_id=user.id,
            filename=safe_filename,
            content_type=content_type,
            status="analyzing",
        )
        event = {"analysis_id": analysis_id, "file_key": file_key, "content_type": content_type}
        # Metadata and the publication intent commit atomically. Object storage
        # is separate: an upload may remain orphaned if this transaction fails.
        db.add(new_analysis)
        db.add(OutboxEvent(analysis_id=analysis_id, topic="certificate_uploaded", payload=event))
        db.commit()

        analyze_requests_total.labels(outcome="accepted").inc()
        return {"analysis_id": analysis_id, "status": "analyzing", "message": "Certificate stored; processing event queued for delivery"}
    finally:
        analyze_request_duration_seconds.observe(time.monotonic() - start)


@app.get("/status/{analysis_id}", response_model=AnalysisStatusResponse)
def get_status(analysis_id: str, user: User = Depends(require_user), db: Session = Depends(_get_db)):
    analysis = _get_owned_analysis_or_404(db, analysis_id, user)
    return {
        "analysis_id": analysis.id,
        "status": analysis.status,
        "authenticity_score": None,
        "verdict": "Inconclusive" if analysis.status == "completed" else None,
        "ocr_text": analysis.ocr_text,
        "extracted_fields": analysis.extracted_fields,
        "suspicious_signals": analysis.suspicious_signals,
        "confidence": None,
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

    buffer = io.BytesIO()
    create_report(buffer, {
        "file_name": analysis.filename,
        "user": user.email,
        "verdict": "Inconclusive",
        "signals": ", ".join(analysis.suspicious_signals or []) or "None detected",
        "ocr_preview": (analysis.ocr_text or "")[:1500],
        "fields": analysis.extracted_fields or {},
    })
    pdf_bytes = buffer.getvalue()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=CertiFake_Report_{analysis_id}.pdf"},
    )
