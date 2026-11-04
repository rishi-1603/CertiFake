"""OCR consumer and transactional outbox dispatcher.

The OCR row update and downstream event are one database transaction. The
background dispatcher also publishes the upload and completion outbox events.
Redelivery may duplicate broker messages; stage processing is guarded in DB.
"""
import os
import tempfile

from app.consumer import DUPLICATE, HANDLED, consume_loop
from app.event_validation import (
    InvalidEventError,
    parse_document_event,
    validate_document_reference,
    validate_queued_reference,
)
from app.kafka_utils import get_kafka_consumer, get_kafka_producer
from app.models import CertificateAnalysis, OutboxEvent, SessionLocal
from app.ocr import extract_fields, run_ocr
from app.s3_utils import download_file_bytes

SOURCE_TOPIC = "certificate_uploaded"
WORKER_NAME = "ocr"

consumer = get_kafka_consumer("ocr-worker-group", [SOURCE_TOPIC])
producer = get_kafka_producer()


def _mark_failed(analysis_id: str, error: Exception | None) -> None:
    """Terminal state, called only once retries are exhausted (not per attempt).

    Deliberately does NOT record the error text on the row: CertificateAnalysis
    has no error_message column, and this project has no migration tooling
    (app/models.py uses Base.metadata.create_all, which creates missing
    tables but never ALTERs existing ones), so adding one here would leave
    every already-deployed database without the column and break at runtime.
    The error detail is not lost -- it is preserved durably in the
    dead-letter envelope (app/kafka_utils.py:publish_to_dlq stores the
    message, error type, attempt count and timestamp), which is the right
    place for it anyway: the row is for client-facing status, the DLQ is for
    operator-facing diagnosis and replay.
    """
    if not analysis_id or isinstance(error, InvalidEventError):
        return
    db = SessionLocal()
    try:
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).with_for_update().first()
        if analysis is not None and analysis.status != "completed":
            analysis.status = "failed"
            db.commit()
    finally:
        db.close()


def process_ocr(event: dict) -> str:
    """Handle one `certificate_uploaded` event. Returns HANDLED or DUPLICATE; raises on failure.

    Raising (rather than swallowing) is what lets app/consumer.py retry and,
    once exhausted, dead-letter. The old version caught every exception here
    and marked the row failed immediately, which made a transient MinIO or
    database blip permanently fatal to that certificate.
    """
    analysis_id, file_key, content_type = parse_document_event(event)

    db = SessionLocal()
    tmp_path = None
    try:
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).with_for_update().first()
        if analysis is None:
            # Nothing to update and nothing to retry -- the row is gone (or
            # never existed). Treat as handled so the offset commits; raising
            # here would only dead-letter a message that can never succeed.
            return HANDLED

        # DB row lock serializes overlapping deliveries on PostgreSQL.
        # Non-None text means this stage and its outbox insert committed.
        validate_document_reference(analysis, analysis_id, file_key, content_type)
        validate_queued_reference(db, SOURCE_TOPIC, analysis_id, file_key, content_type)
        if analysis.ocr_text is not None:
            return DUPLICATE

        file_bytes = download_file_bytes(file_key)

        ext = ".pdf" if content_type == "application/pdf" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        ocr_text = run_ocr(tmp_path, content_type)
        extracted = extract_fields(ocr_text)

        analysis.ocr_text = ocr_text[:4000]
        analysis.extracted_fields = extracted
        db.add(OutboxEvent(analysis_id=analysis_id, topic="ocr_completed", payload={
            "analysis_id": analysis_id, "file_key": file_key,
            "content_type": content_type,
        }))
        db.commit()
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)
        db.close()

    print(f"[OCR] Completed {analysis_id}")
    return HANDLED


def main() -> None:
    # A separate producer/DB session publishes the transactionally queued handoff.
    from threading import Thread

    from app.outbox import dispatch_loop
    Thread(target=dispatch_loop, name="ocr-outbox", daemon=True).start()
    consume_loop(
        consumer, producer, process_ocr,
        source_topic=SOURCE_TOPIC,
        worker_name=WORKER_NAME,
        on_dead_letter=lambda event, error, attempts: _mark_failed(event.get("analysis_id", ""), error),
    )


if __name__ == "__main__":
    # Metrics-only HTTP server: this worker is a bare consumer loop with no
    # HTTP server of its own, but monitoring/prometheus.yml targets
    # "worker-ocr:8000" and expects something to answer /metrics.
    from prometheus_client import start_http_server

    start_http_server(8000)
    print("[OCR] Worker started. Waiting for events...")
    main()
