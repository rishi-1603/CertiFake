"""Forensics worker: consumes `ocr_completed`, scores the document, publishes `analysis_completed`.

Day 4 rework, mirroring app/workers/ocr.py: retry/backoff, dead-lettering,
manual offset commit and duplicate-event idempotency are shared code in
app/consumer.py rather than a second copy of a hand-rolled poll loop. This
file keeps only what is forensics-specific -- download, score, heatmap
upload, persist, publish -- plus its own idempotency rule.
"""
import base64
import os
import tempfile

from app.consumer import DUPLICATE, HANDLED, consume_loop
from app.forensics import heatmap_b64, score_document
from app.kafka_utils import get_kafka_consumer, get_kafka_producer, produce_event
from app.models import CertificateAnalysis, SessionLocal
from app.s3_utils import download_file_bytes, upload_file_bytes

SOURCE_TOPIC = "ocr_completed"
WORKER_NAME = "forensics"

consumer = get_kafka_consumer("forensics-worker-group", [SOURCE_TOPIC])
producer = get_kafka_producer()


def _mark_failed(analysis_id: str, error: Exception | None) -> None:
    """Terminal state, called only once retries are exhausted (not per attempt).

    See app/workers/ocr.py:_mark_failed for why the error text is not stored
    on the row (no such column, and no migration tooling to add one safely)
    and where it lives instead (the dead-letter envelope).
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


def process_forensics(event: dict) -> str:
    """Handle one `ocr_completed` event. Returns HANDLED or DUPLICATE; raises on failure."""
    analysis_id = event["analysis_id"]
    file_key = event["file_key"]
    content_type = event["content_type"]
    ocr_text = event.get("ocr_text", "")

    db = SessionLocal()
    tmp_path = None
    try:
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis is None:
            # Row gone (or never existed): nothing to score and no retry can
            # create it. Commit rather than dead-letter an unfixable message.
            return HANDLED

        # Idempotency: at-least-once delivery can redeliver this event after
        # a crash between publishing analysis_completed and committing the
        # offset. "completed" means this stage already ran to completion.
        if analysis.status == "completed":
            return DUPLICATE

        file_bytes = download_file_bytes(file_key)

        ext = ".pdf" if content_type == "application/pdf" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        score, signals, gray = score_document(tmp_path, ocr_text, content_type)
        heat = heatmap_b64(gray)

        if score >= 80:
            verdict = "Likely Genuine"
        elif score >= 55:
            verdict = "Needs Review"
        else:
            verdict = "Likely Fake"

        confidence = round(score / 100.0, 2)

        # Heatmap goes to the same "{user_id}/{analysis_id}/" prefix as the
        # original upload, derived from file_key's own directory rather than
        # reconstructed, so GET /heatmap/{analysis_id} in app/api.py (which
        # looks for "{user.id}/{analysis_id}/heatmap.png") keeps matching
        # even if the key layout changes again.
        heatmap_key = f"{os.path.dirname(file_key)}/heatmap.png"
        if heat:
            upload_file_bytes(heatmap_key, base64.b64decode(heat))

        analysis.authenticity_score = score
        analysis.verdict = verdict
        analysis.suspicious_signals = signals
        analysis.confidence = confidence
        analysis.status = "completed"
        db.commit()
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)
        db.close()

    produce_event(producer, "analysis_completed", analysis_id, {"analysis_id": analysis_id})
    print(f"[Forensics] Completed {analysis_id}")
    return HANDLED


def main() -> None:
    consume_loop(
        consumer, producer, process_forensics,
        source_topic=SOURCE_TOPIC,
        worker_name=WORKER_NAME,
        on_dead_letter=lambda event, error, attempts: _mark_failed(event.get("analysis_id", ""), error),
    )


if __name__ == "__main__":
    # Metrics-only HTTP server: see app/workers/ocr.py -- monitoring/
    # prometheus.yml targets "worker-forensics:8000".
    from prometheus_client import start_http_server

    start_http_server(8000)
    print("[Forensics] Worker started. Waiting for events...")
    main()
