"""Tests for the shared Kafka consumer loop (app/consumer.py) and DLQ plumbing.

No broker is involved: the consumer/producer are fakes, and
`publish_to_dlq` is monkeypatched where the test is about the loop's
decision-making (commit vs. don't commit), or `produce_event` is
monkeypatched where the test is about the DLQ envelope itself. These tests
target the Day 4 guarantees: bounded retry with backoff, dead-lettering
after exhaustion, offsets committed at exactly the right moments, and
duplicate-event idempotency.
"""
import json

import pytest

import app.consumer as consumer_module
import app.kafka_utils as kafka_utils
from app.consumer import DUPLICATE, HANDLED, consume_loop, handle_one_message


class FakeMessage:
    def __init__(self, value: bytes, error=None):
        self._value = value
        self._error = error

    def value(self):
        return self._value

    def error(self):
        return self._error


class FakeConsumer:
    """poll() drains a preset list, then returns None forever."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.committed = []

    def poll(self, timeout=1.0):
        return self._messages.pop(0) if self._messages else None

    def commit(self, msg):
        self.committed.append(msg)


def _event_msg(analysis_id="a1", **extra):
    payload = {"analysis_id": analysis_id, "file_key": f"u/{analysis_id}/c.png", "content_type": "image/png"}
    payload.update(extra)
    return FakeMessage(json.dumps(payload).encode())


@pytest.fixture
def fake_dlq(monkeypatch):
    """Capture publish_to_dlq calls; default = DLQ publish succeeds."""
    calls = []
    state = {"ok": True}

    def _fake(producer, source_topic, original_event, error, attempts):
        calls.append({"topic": source_topic, "event": original_event, "error": error, "attempts": attempts})
        return state["ok"]

    monkeypatch.setattr(consumer_module, "publish_to_dlq", _fake)
    return {"calls": calls, "state": state}


@pytest.fixture
def no_sleep(monkeypatch):
    """Record backoff sleeps without actually waiting."""
    slept = []
    monkeypatch.setattr(consumer_module, "_backoff_seconds", lambda *a, **k: 0.01)
    return slept


def _handle(msg, handler, fake_dlq, **kw):
    return handle_one_message(
        FakeConsumer([]), object(), handler, msg,
        source_topic="certificate_uploaded", worker_name="test",
        sleep=lambda s: None, **kw,
    )


def test_success_on_first_attempt_commits_and_does_not_dlq(fake_dlq):
    consumer = FakeConsumer([])
    msg = _event_msg()
    outcome = handle_one_message(
        consumer, object(), lambda event: HANDLED, msg,
        source_topic="certificate_uploaded", worker_name="test", sleep=lambda s: None,
    )
    assert outcome == "handled"
    assert consumer.committed == [msg]
    assert fake_dlq["calls"] == []


def test_transient_failure_then_success_retries_and_commits(fake_dlq):
    attempts = []

    def flaky(event):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("MinIO blip")
        return HANDLED

    consumer = FakeConsumer([])
    msg = _event_msg()
    slept = []
    outcome = handle_one_message(
        consumer, object(), flaky, msg,
        source_topic="certificate_uploaded", worker_name="test",
        max_attempts=3, sleep=slept.append,
    )
    assert outcome == "handled"
    assert len(attempts) == 3
    # Backoff slept between attempts only -- twice for three attempts, never
    # after the final successful one.
    assert len(slept) == 2
    assert consumer.committed == [msg]
    assert fake_dlq["calls"] == []


def test_exhausted_attempts_dead_letters_original_event_and_commits(fake_dlq):
    def always_fails(event):
        raise RuntimeError("permanent boom")

    terminal = []
    consumer = FakeConsumer([])
    msg = _event_msg(analysis_id="doomed")
    outcome = handle_one_message(
        consumer, object(), always_fails, msg,
        source_topic="certificate_uploaded", worker_name="test",
        max_attempts=3, sleep=lambda s: None,
        on_dead_letter=lambda event, error, attempts: terminal.append((event, error, attempts)),
    )

    assert outcome == "dead_lettered"
    assert len(fake_dlq["calls"]) == 1
    call = fake_dlq["calls"][0]
    # The ORIGINAL event must survive intact for inspection/replay -- not a
    # summary, and not just the analysis id.
    assert call["event"]["analysis_id"] == "doomed"
    assert call["event"]["file_key"] == "u/doomed/c.png"
    assert call["attempts"] == 3
    assert isinstance(call["error"], RuntimeError)
    # Committed: leaving it uncommitted would redeliver a known-poison
    # message forever, blocking the partition.
    assert consumer.committed == [msg]
    # Terminal callback fired exactly once, after exhaustion -- not per attempt.
    assert len(terminal) == 1
    assert terminal[0][2] == 3


def test_failed_dlq_publish_does_not_commit(fake_dlq):
    """The critical ordering guarantee: if the message cannot be
    dead-lettered, the offset must NOT be committed. Committing would lose
    it from both the source topic and the DLQ."""
    fake_dlq["state"]["ok"] = False

    consumer = FakeConsumer([])
    msg = _event_msg()
    outcome = handle_one_message(
        consumer, object(), lambda event: (_ for _ in ()).throw(RuntimeError("boom")), msg,
        source_topic="certificate_uploaded", worker_name="test",
        max_attempts=2, sleep=lambda s: None,
    )
    assert outcome == "dead_letter_failed"
    assert consumer.committed == []


def test_duplicate_event_is_committed_without_dead_lettering(fake_dlq):
    consumer = FakeConsumer([])
    msg = _event_msg()
    outcome = handle_one_message(
        consumer, object(), lambda event: DUPLICATE, msg,
        source_topic="certificate_uploaded", worker_name="test", sleep=lambda s: None,
    )
    assert outcome == "duplicate"
    assert consumer.committed == [msg]
    assert fake_dlq["calls"] == []


def test_malformed_payload_dead_letters_immediately_without_burning_retries(fake_dlq):
    attempts = []

    def handler(event):
        attempts.append(1)
        return HANDLED

    consumer = FakeConsumer([])
    msg = FakeMessage(b"{not valid json")
    outcome = handle_one_message(
        consumer, object(), handler, msg,
        source_topic="certificate_uploaded", worker_name="test",
        max_attempts=3, sleep=lambda s: None,
    )
    assert outcome == "malformed_dead_lettered"
    # Retrying an undecodable payload can never succeed.
    assert attempts == []
    assert consumer.committed == [msg]
    assert "unparseable_payload" in fake_dlq["calls"][0]["event"]


def test_broken_dead_letter_callback_still_dead_letters(fake_dlq):
    """A bug in the worker's terminal callback must not prevent the message
    from reaching the DLQ -- that would turn one failure into data loss."""
    def bad_callback(event, error, attempts):
        raise ValueError("callback is broken")

    consumer = FakeConsumer([])
    outcome = handle_one_message(
        consumer, object(), lambda event: (_ for _ in ()).throw(RuntimeError("boom")), _event_msg(),
        source_topic="certificate_uploaded", worker_name="test",
        max_attempts=1, sleep=lambda s: None, on_dead_letter=bad_callback,
    )
    assert outcome == "dead_lettered"
    assert len(fake_dlq["calls"]) == 1


def test_consume_loop_processes_a_queue_then_stops(fake_dlq):
    msgs = [_event_msg("a"), _event_msg("b")]
    consumer = FakeConsumer(msgs)
    seen = []

    def handler(event):
        seen.append(event["analysis_id"])
        return HANDLED

    consume_loop(
        consumer, object(), handler,
        source_topic="certificate_uploaded", worker_name="test",
        poll_timeout=0, sleep=lambda s: None,
        should_run=lambda: len(seen) < 2,
    )
    assert seen == ["a", "b"]
    assert consumer.committed == msgs


def test_consume_loop_skips_consumer_errors_without_dying(fake_dlq):
    err = FakeMessage(b"", error="broker partition error")
    good = _event_msg("ok")
    consumer = FakeConsumer([err, good])
    seen = []

    consume_loop(
        consumer, object(), lambda event: seen.append(event["analysis_id"]) or HANDLED,
        source_topic="certificate_uploaded", worker_name="test",
        poll_timeout=0, sleep=lambda s: None,
        should_run=lambda: len(seen) < 1,
    )
    assert seen == ["ok"]


def test_backoff_grows_and_is_capped():
    """Exponential growth, bounded by the cap -- an uncapped backoff would
    eventually sleep for hours and look like a hung worker."""
    from app.consumer import _backoff_seconds

    # With full jitter the value is random in [0, min(cap, base*2^(n-1))],
    # so assert on the upper bound across many samples.
    for attempt in range(1, 8):
        samples = [_backoff_seconds(attempt, 0.5, 8.0) for _ in range(200)]
        ceiling = min(8.0, 0.5 * (2 ** (attempt - 1)))
        assert all(0 <= s <= ceiling + 1e-9 for s in samples), (attempt, max(samples), ceiling)
    # And the cap actually binds at high attempt counts.
    high = [_backoff_seconds(20, 0.5, 8.0) for _ in range(200)]
    assert max(high) <= 8.0


def test_dlq_envelope_contents_and_topic_name(monkeypatch):
    """Unit test of publish_to_dlq itself: topic naming and envelope shape."""
    published = []

    def fake_produce(producer, topic, key, value_dict):
        published.append((topic, key, value_dict))
        return True

    monkeypatch.setattr(kafka_utils, "produce_event", fake_produce)

    assert kafka_utils.dlq_topic_for("certificate_uploaded") == "certificate_uploaded.dlq"

    ok = kafka_utils.publish_to_dlq(
        object(), "certificate_uploaded",
        {"analysis_id": "xyz", "file_key": "u/xyz/c.png"},
        ValueError("bad pixels"), attempts=3,
    )
    assert ok is True
    topic, key, envelope = published[0]
    assert topic == "certificate_uploaded.dlq"
    assert key == "xyz"
    assert envelope["source_topic"] == "certificate_uploaded"
    assert envelope["original_event"] == {"analysis_id": "xyz", "file_key": "u/xyz/c.png"}
    assert envelope["error"] == "bad pixels"
    assert envelope["error_type"] == "ValueError"
    assert envelope["attempts"] == 3
    assert envelope["dead_lettered_at"]


def test_consumer_is_configured_for_manual_commit(monkeypatch):
    """Regression test for the core Day 4 fix: confluent_kafka defaults
    enable.auto.commit to True, which commits offsets on a timer regardless
    of whether processing succeeded and can silently lose messages."""
    captured = {}

    class FakeKafkaConsumer:
        def __init__(self, conf):
            captured.update(conf)

        def subscribe(self, topics):
            captured["topics"] = topics

    monkeypatch.setattr(kafka_utils, "Consumer", FakeKafkaConsumer)
    kafka_utils.get_kafka_consumer("test-group", ["certificate_uploaded"])

    assert captured["enable.auto.commit"] is False
    assert captured["group.id"] == "test-group"
    assert captured["topics"] == ["certificate_uploaded"]
