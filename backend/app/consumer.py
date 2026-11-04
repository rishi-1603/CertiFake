"""Shared Kafka consumer loop: bounded retry, dead-lettering, manual commit.

Both workers (app/workers/ocr.py, app/workers/forensics.py) previously had
their own near-identical `while True: poll(); process()` loop with the same
weaknesses: no retry (one exception meant the analysis was permanently
marked failed), no dead-letter topic, and no offset commit at all -- which,
combined with confluent_kafka's default auto-commit, meant at-most-once
delivery and silently lost messages. See get_kafka_consumer's docstring in
app/kafka_utils.py for the auto-commit problem specifically.

This module owns all four concerns in one tested place instead of twice:

1. **Bounded retry with exponential backoff + jitter.** A transient failure
   (MinIO hiccup, DB deadlock, a worker restarting mid-publish) gets
   several attempts before giving up. Jitter matters: without it, many
   workers retrying the same upstream outage would wake in lockstep and
   re-overload whatever just recovered.

2. **Dead-lettering.** Once attempts are exhausted the ORIGINAL event is
   published to `<topic>.dlq` with the error, error type, attempt count and
   a timestamp -- inspectable and replayable by hand, not reduced to a log
   line nobody will find.

3. **Manual commit at exactly the right points.** Commit after a message is
   handled, or after it is dead-lettered. Deliberately NOT committed when
   the DLQ publish itself failed: committing then would lose the message
   from both the source topic and the DLQ, which is the precise failure
   this exists to prevent. Leaving it uncommitted means redelivery.

4. **Idempotency.** At-least-once delivery makes duplicates normal (a crash
   after processing but before committing redelivers the same event), so a
   handler may report "duplicate" and this loop counts it as success without
   re-running the stage. Retrying and deduplicating are not independently
   optional -- you cannot safely do the first without the second.

Handlers follow a small contract: take the parsed event dict, return
HANDLED or DUPLICATE, and raise on failure. Keeping the loop generic means
a future third worker gets retry/DLQ/commit correctness for free rather
than re-deriving it.
"""
import hashlib
import json
import logging
import random
import time

from app.kafka_utils import dlq_topic_for, publish_to_dlq
from app.metrics import (
    worker_duplicate_events_skipped_total,
    worker_event_attempts_total,
    worker_events_dead_lettered_total,
    worker_events_failed_total,
    worker_events_processed_total,
)

logger = logging.getLogger("certifake.consumer")

#: Handler return values.
HANDLED = "handled"
DUPLICATE = "duplicate"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_BACKOFF_CAP_SECONDS = 8.0


def _backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter, capped.

    attempt is 1-based, so the first retry waits ~base seconds. Full jitter
    (uniform 0..exponential) rather than a fixed delay: it spreads retries
    across the window so recovering infrastructure isn't hit by a
    synchronised thundering herd of workers all waking at once.
    """
    exponential = min(cap, base * (2 ** (attempt - 1)))
    return random.uniform(0, exponential)


def _commit_sync(consumer, message):
    partitions = consumer.commit(message, asynchronous=False)
    if partitions:
        for partition in partitions:
            if partition.error is not None:
                raise RuntimeError("Kafka offset commit failed")


def handle_one_message(
    consumer,
    producer,
    handler,
    raw_message,
    *,
    source_topic: str,
    worker_name: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    backoff_cap_seconds: float = DEFAULT_BACKOFF_CAP_SECONDS,
    sleep=time.sleep,
    on_dead_letter=None,
) -> str:
    """Process a single delivered message end-to-end. Returns an outcome string.

    Outcomes: "handled", "duplicate", "dead_lettered", "dead_letter_failed",
    "malformed_dead_lettered", "malformed_dead_letter_failed".

    Commits the offset in every case except the two `*_failed` ones, where
    not committing is the deliberate choice (redelivery beats loss).
    """
    try:
        event = json.loads(raw_message.value().decode("utf-8"))
        if not isinstance(event, dict):
            raise ValueError("Event payload must be a JSON object")
    except (ValueError, UnicodeDecodeError, AttributeError) as exc:
        # Undecodable payload: retrying cannot possibly help, so go straight
        # to the DLQ with a digest and length rather than storing raw content.
        logger.error("Malformed message on topic=%s: %s", source_topic, exc)
        raw = raw_message.value() or b""
        if not isinstance(raw, bytes):
            raw = b""
        ok = publish_to_dlq(
            producer, source_topic,
            {"unparseable_sha256": hashlib.sha256(raw).hexdigest(), "payload_bytes": len(raw)},
            exc, attempts=0,
        )
        outcome = "malformed_dead_lettered" if ok else "malformed_dead_letter_failed"
        if ok:
            _commit_sync(consumer, raw_message)
        return outcome

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        worker_event_attempts_total.labels(worker=worker_name, attempt=str(attempt)).inc()
        try:
            result = handler(event)
        except Exception as exc:  # noqa: BLE001 -- any failure must be counted and retried, not crash the loop
            last_error = exc
            worker_events_failed_total.labels(worker=worker_name).inc()
            logger.warning(
                "Attempt %d/%d failed for worker=%s topic=%s: %s",
                attempt, max_attempts, worker_name, source_topic, type(exc).__name__,
            )
            if attempt < max_attempts:
                sleep(_backoff_seconds(attempt, backoff_base_seconds, backoff_cap_seconds))
            continue

        if result == DUPLICATE:
            worker_duplicate_events_skipped_total.labels(worker=worker_name).inc()
            logger.info("Skipping already-completed stage (worker=%s) for event=%s", worker_name, event.get("analysis_id"))
            _commit_sync(consumer, raw_message)
            return "duplicate"

        worker_events_processed_total.labels(worker=worker_name).inc()
        _commit_sync(consumer, raw_message)
        return "handled"

    # Every attempt exhausted: dead-letter the original event.
    worker_events_dead_lettered_total.labels(worker=worker_name).inc()
    dlq_ok = publish_to_dlq(producer, source_topic, event, last_error, attempts=max_attempts)
    if not dlq_ok:
        return "dead_letter_failed"
    if on_dead_letter is not None:
        try:
            on_dead_letter(event, last_error, max_attempts)
        except Exception as cb_exc:
            # A durable DLQ alone does not persist the user-facing terminal
            # state. Do not advance the offset if that write failed; a retry
            # may duplicate the DLQ message, which operators must deduplicate.
            logger.error("Could not persist terminal state: %s", type(cb_exc).__name__)
            return "dead_letter_failed"
    _commit_sync(consumer, raw_message)
    return "dead_lettered"


def consume_loop(
    consumer,
    producer,
    handler,
    *,
    source_topic: str,
    worker_name: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    backoff_cap_seconds: float = DEFAULT_BACKOFF_CAP_SECONDS,
    poll_timeout: float = 1.0,
    sleep=time.sleep,
    should_run=None,
    on_dead_letter=None,
) -> None:
    """Run the poll/dispatch loop forever (or until `should_run()` is False).

    `should_run` exists so tests can drive a bounded number of iterations
    against fake consumer/producer objects instead of needing a broker; in
    production it is left as None and the loop never exits.
    """
    logger.info(
        "Worker=%s consuming topic=%s (DLQ=%s, max_attempts=%d, auto-commit disabled)",
        worker_name, source_topic, dlq_topic_for(source_topic), max_attempts,
    )
    while should_run is None or should_run():
        msg = consumer.poll(poll_timeout)
        if msg is None:
            continue
        if msg.error():
            # Broker/partition-level errors (e.g. _PARTITION_EOF) are not
            # message failures; log and keep polling rather than dying.
            logger.error("Consumer error on topic=%s: %s", source_topic, msg.error())
            continue
        outcome = handle_one_message(
            consumer, producer, handler, msg,
            source_topic=source_topic,
            worker_name=worker_name,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_cap_seconds=backoff_cap_seconds,
            sleep=sleep,
            on_dead_letter=on_dead_letter,
        )

        if outcome in ("dead_letter_failed", "malformed_dead_letter_failed"):
            # Polling the next record and committing it could advance the partition
            # beyond this uncommitted offset. Stop the process for broker redelivery.
            raise RuntimeError("DLQ delivery failed; stopping with uncommitted offset")
