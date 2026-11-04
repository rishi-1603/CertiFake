"""Transactional document-event outbox (at-least-once intent, not exactly-once).

The publisher holds a database row lock during a bounded broker call. If the
broker acknowledges but the DB commit fails, publication can repeat; consumers
must remain idempotent. An unconfirmed publish leaves the row pending.
"""
import logging
import time
from datetime import datetime, timezone

from app.kafka_utils import get_kafka_producer, produce_event
from app.models import OutboxEvent, SessionLocal

logger = logging.getLogger("certifake.outbox")


def dispatch_once(producer) -> str:
    db = SessionLocal()
    try:
        row = (db.query(OutboxEvent).filter(OutboxEvent.sent_at.is_(None))
               .order_by(OutboxEvent.created_at, OutboxEvent.id)
               .with_for_update(skip_locked=True).first())
        if row is None:
            db.rollback()
            return "empty"
        if not produce_event(producer, row.topic, row.analysis_id, row.payload):
            db.rollback()
            return "pending"
        row.sent_at = datetime.now(timezone.utc)
        db.commit()
        return "sent"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def dispatch_loop() -> None:
    producer = get_kafka_producer()
    while True:
        try:
            state = dispatch_once(producer)
        except Exception as exc:
            logger.error("Outbox dispatch failed (%s); event remains pending for retry", type(exc).__name__)
            state = "pending"
        if state != "sent":
            time.sleep(2)
