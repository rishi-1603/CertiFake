"""OCR worker: consumes `certificate_uploaded`, extracts text, publishes `ocr_completed`.

Day 4 rework: retry/backoff, dead-lettering, manual offset commit and
duplicate-event idempotency now live in app/consumer.py (shared with the
forensics worker) instead of being re-implemented here. Before that this
file had its own `while True: poll()` loop in which a single exception
permanently marked the analysis failed -- no retry, no DLQ -- and it never
committed offsets, which under confluent_kafka's default auto-commit meant
messages could be silently lost on a crash.

What this file keeps is the part that is genuinely OCR-specific: the
download/extract/persist/publish sequence, and the idempotency rule that
decides whether this stage already ran.
"""
import os
import tempfile

from app.consumer import DUPLICATE, HANDLED, consume_loop
from app.kafka_utils import get_kafka_consumer, get_kafka_producer, produce_event
from app.models import CertificateAnalysis, SessionLocal
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
    if not analysis_id:
        return
    db = SessionLocal()
    try:
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis is not None:
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
    analysis_id = event["analysis_id"]
    file_key = event["file_key"]
    content_type = event["content_type"]

    db = SessionLocal()
    tmp_path = None
    try:
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis is None:
            # Nothing to update and nothing to retry -- the row is gone (or
            # never existed). Treat as handled so the offset commits; raising
            # here would only dead-letter a message that can never succeed.
            return HANDLED

        # Idempotency: manual commit gives at-least-once delivery, so this
        # same event can legitimately arrive twice (a crash after publishing
        # ocr_completed but before committing). If OCR text is already
        # stored, this stage ran -- skip it rather than re-doing expensive
        # OCR and re-publishing a duplicate downstream event.
        if analysis.ocr_text:
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
        db.commit()
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)
        db.close()

    # Published after the DB commit so a redelivery is detected by the
    # idempotency check above rather than producing a second ocr_completed.
    produce_event(
        producer, "ocr_completed", analysis_id,
        {"analysis_id": analysis_id, "file_key": file_key, "content_type": content_type, "ocr_text": ocr_text},
    )
    print(f"[OCR] Completed {analysis_id}")
    return HANDLED


def main() -> None:
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
