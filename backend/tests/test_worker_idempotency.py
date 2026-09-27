"""Worker-level idempotency: a redelivered event must not re-run its stage.

Manual offset commit (the Day 4 fix in app/kafka_utils.get_kafka_consumer)
gives at-least-once delivery, which makes duplicate delivery NORMAL rather
than exceptional -- a crash after a worker finishes its stage but before it
commits the offset causes Kafka to redeliver the same event. Without these
guards a duplicate would redo expensive OCR/forensics and publish a second
downstream event, double-processing one certificate.

These tests drive the real worker handlers against the real (test) database
and assert the expensive side effects do NOT happen on a duplicate.
"""
import app.workers.forensics as forensics_worker
import app.workers.ocr as ocr_worker
from app.consumer import DUPLICATE
from app.models import CertificateAnalysis, SessionLocal


def _make_analysis(**kwargs):
    db = SessionLocal()
    try:
        row = CertificateAnalysis(
            id=kwargs.pop("id", "idem-1"),
            user_id=None,
            filename="cert.png",
            content_type="image/png",
            status=kwargs.pop("status", "analyzing"),
            **kwargs,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _event(analysis_id):
    return {
        "analysis_id": analysis_id,
        "file_key": f"u/{analysis_id}/cert.png",
        "content_type": "image/png",
        "ocr_text": "some extracted text",
    }


def test_ocr_stage_skips_when_text_already_extracted(monkeypatch):
    _make_analysis(id="ocr-dup", ocr_text="already extracted earlier")

    def explode(file_key):
        raise AssertionError("download_file_bytes must not be called for a duplicate event")

    monkeypatch.setattr(ocr_worker, "download_file_bytes", explode)
    monkeypatch.setattr(ocr_worker, "run_ocr", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OCR re-run")))
    monkeypatch.setattr(
        ocr_worker, "produce_event",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate ocr_completed published")),
    )

    assert ocr_worker.process_ocr(_event("ocr-dup")) == DUPLICATE


def test_forensics_stage_skips_when_analysis_already_completed(monkeypatch):
    _make_analysis(id="for-dup", status="completed", authenticity_score=91.0, verdict="Likely Genuine")

    monkeypatch.setattr(
        forensics_worker, "download_file_bytes",
        lambda file_key: (_ for _ in ()).throw(AssertionError("must not re-download for a duplicate")),
    )
    monkeypatch.setattr(
        forensics_worker, "score_document",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-score a duplicate")),
    )
    monkeypatch.setattr(
        forensics_worker, "produce_event",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate analysis_completed published")),
    )

    assert forensics_worker.process_forensics(_event("for-dup")) == DUPLICATE


def test_missing_analysis_row_is_handled_not_dead_lettered(monkeypatch):
    """A redelivery for a row that no longer exists can never succeed, so it
    must be reported HANDLED (commit and move on) rather than raising --
    raising would dead-letter a message no retry can ever fix."""
    monkeypatch.setattr(
        ocr_worker, "download_file_bytes",
        lambda file_key: (_ for _ in ()).throw(AssertionError("should not reach storage")),
    )
    assert ocr_worker.process_ocr(_event("no-such-row")) == "handled"


def test_dead_letter_marks_analysis_failed_once():
    """The terminal callback sets status=failed -- and is invoked by
    app/consumer.py only after retries are exhausted, not per attempt."""
    _make_analysis(id="terminal-1", status="analyzing")
    ocr_worker._mark_failed("terminal-1", RuntimeError("boom"))

    db = SessionLocal()
    try:
        row = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == "terminal-1").first()
        assert row.status == "failed"
    finally:
        db.close()


def test_mark_failed_ignores_blank_and_unknown_ids():
    # Must not raise on the malformed-payload path, where there is no
    # analysis id to update.
    ocr_worker._mark_failed("", RuntimeError("boom"))
    forensics_worker._mark_failed("", RuntimeError("boom"))
    ocr_worker._mark_failed("never-existed", RuntimeError("boom"))
