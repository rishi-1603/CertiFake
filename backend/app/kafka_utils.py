import json
import logging
import os
from datetime import datetime, timezone

from confluent_kafka import Consumer, KafkaException, Producer

logger = logging.getLogger("certifake.kafka")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# How long produce_event will wait for the broker to accept the message
# before giving up. Producer.flush() with no timeout blocks *indefinitely*
# if the broker is unreachable, which would hang the /analyze request (and
# the client behind it) forever during a Kafka outage -- a bounded timeout
# turns "Kafka is down" into a fast, handled failure instead of a hang.
PRODUCE_TIMEOUT_SECONDS = float(os.getenv("KAFKA_PRODUCE_TIMEOUT_SECONDS", "5"))


def get_kafka_producer():
    conf = {"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS}
    return Producer(conf)


def produce_event(producer, topic, key, value_dict) -> bool:
    """Publish an event. Returns True on confirmed delivery, False otherwise.

    Never raises: callers (see app/api.py:analyze) are expected to treat a
    False return as "the DB record and uploaded file are still safely
    persisted, but no worker will pick this up automatically yet" and handle
    it explicitly rather than have an unrelated exception surface as a raw
    500 to the client.
    """
    delivery_result = {"delivered": False, "error": None}

    def _on_delivery(err, msg):
        if err is not None:
            delivery_result["error"] = err
        else:
            delivery_result["delivered"] = True

    try:
        producer.produce(
            topic,
            key=str(key).encode("utf-8"),
            value=json.dumps(value_dict).encode("utf-8"),
            callback=_on_delivery,
        )
        remaining = producer.flush(timeout=PRODUCE_TIMEOUT_SECONDS)
        if remaining > 0:
            logger.error(
                "Kafka publish to topic=%s key=%s timed out after %.1fs (%d messages still queued)",
                topic, key, PRODUCE_TIMEOUT_SECONDS, remaining,
            )
            return False
        if delivery_result["error"] is not None:
            logger.error("Kafka publish to topic=%s key=%s failed: %s", topic, key, delivery_result["error"])
            return False
        return delivery_result["delivered"]
    except (KafkaException, BufferError) as exc:
        logger.error("Kafka publish to topic=%s key=%s raised %s: %s", topic, key, type(exc).__name__, exc)
        return False


def get_kafka_consumer(group_id, topics):
    """Create a subscribed consumer with MANUAL offset commit.

    `enable.auto.commit` is explicitly False. This is a correctness fix,
    not a preference: confluent_kafka defaults auto-commit to TRUE with a
    5-second interval, which commits offsets on a *timer* regardless of
    whether the message was actually processed. Under that default a worker
    that crashed (or hung, or raised) mid-message could already have had
    that message's offset committed, so Kafka would never redeliver it --
    the upload would sit in status="analyzing" forever and the event would
    be silently, permanently lost. That is at-most-once delivery.

    The README previously claimed a crashed worker "relies on Kafka's own
    consumer-group rebalance/redelivery". With auto-commit on, that safety
    net did not actually exist. Committing only after a message has been
    handled (or dead-lettered) gives at-least-once delivery instead -- see
    app/consumer.py, which owns the commit points.

    At-least-once means duplicates become possible (a crash after
    processing but before committing redelivers the same event), so
    app/consumer.py also enforces per-stage idempotency. The two changes
    are not independently optional.
    """
    conf = {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
    c = Consumer(conf)
    c.subscribe(topics)
    return c


def dlq_topic_for(topic: str) -> str:
    """Dead-letter topic name for a source topic."""
    return f"{topic}.dlq"


def publish_to_dlq(producer, source_topic, original_event, error, attempts) -> bool:
    """Move a poison message to `<source_topic>.dlq`.

    Called only after all in-process retry attempts are exhausted. The
    envelope keeps the ORIGINAL event intact plus why/when it failed, so a
    dead-lettered certificate can be inspected and replayed by hand rather
    than being reduced to a log line nobody will find.

    Returns whether the DLQ publish itself was confirmed. If it was not,
    the caller must NOT commit the source offset -- otherwise the message
    would vanish from both the source topic and the DLQ, which is the exact
    failure mode this exists to prevent.
    """
    envelope = {
        "source_topic": source_topic,
        "original_event": original_event,
        "error": str(error)[:2000],
        "error_type": type(error).__name__,
        "attempts": attempts,
        "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
    }
    key = original_event.get("analysis_id", "unknown") if isinstance(original_event, dict) else "unknown"
    delivered = produce_event(producer, dlq_topic_for(source_topic), key, envelope)
    if delivered:
        logger.error(
            "Message key=%s dead-lettered from topic=%s to topic=%s after %d attempt(s): %s",
            key, source_topic, dlq_topic_for(source_topic), attempts, envelope["error"],
        )
    else:
        logger.critical(
            "Message key=%s exhausted %d attempt(s) on topic=%s AND could not be dead-lettered "
            "(DLQ publish unconfirmed). Offset will not be committed, so it will be redelivered.",
            key, attempts, source_topic,
        )
    return delivered
