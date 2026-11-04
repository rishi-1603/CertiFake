"""Validate worker event references against the persisted analysis record.

Kafka remains a trusted internal boundary and requires access controls; this
check prevents a mismatched object key from being applied to another owner.
"""
import os


class InvalidEventError(ValueError):
    """Event is invalid or conflicts with the persisted owner and metadata."""


def parse_document_event(event: dict) -> tuple[str, str, str]:
    if not isinstance(event, dict):
        raise InvalidEventError("Invalid event payload")
    fields = tuple(event.get(key) for key in ("analysis_id", "file_key", "content_type"))
    if any(not isinstance(value, str) or not value for value in fields):
        raise InvalidEventError("Invalid event fields")
    return fields


def validate_document_reference(analysis, analysis_id: str, file_key: str, content_type: str) -> None:
    owner = analysis.user_id
    filename = analysis.filename
    if (not isinstance(owner, str) or not owner or '/' in owner or '\\' in owner
            or not isinstance(filename, str) or not filename or '/' in filename or '\\' in filename
            or filename in {'.', '..'} or analysis_id != analysis.id or content_type != analysis.content_type):
        raise InvalidEventError("Event does not match stored analysis metadata")
    # Current uploads use original<ext>; older uploads used their basename.
    # Both must stay within the exact persisted owner/analysis namespace.
    prefix = f"{owner}/{analysis.id}/"
    canonical = f"original{os.path.splitext(filename)[1].lower()}"
    if file_key not in {prefix + canonical, prefix + filename}:
        raise InvalidEventError("Event object key does not match stored analysis")


def validate_queued_reference(db, source_topic: str, analysis_id: str, file_key: str, content_type: str) -> None:
    """Match new-format events to the committed outbox; permit bounded legacy keys.

    Old in-flight analyses may not have an upload outbox row. For them the
    owner/key check remains the fallback, not proof of event authenticity.
    """
    from app.models import OutboxEvent

    uploaded = db.query(OutboxEvent).filter_by(analysis_id=analysis_id, topic="certificate_uploaded").first()
    if uploaded is None:
        return
    stage = db.query(OutboxEvent).filter_by(analysis_id=analysis_id, topic=source_topic).first()
    if stage is None or not isinstance(stage.payload, dict):
        raise InvalidEventError("Event has no matching committed outbox entry")
    expected = {"analysis_id": analysis_id, "file_key": file_key, "content_type": content_type}
    if any(stage.payload.get(key) != value for key, value in expected.items()):
        raise InvalidEventError("Event does not match the committed outbox entry")
