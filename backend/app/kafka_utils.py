import os
import json
import logging
from confluent_kafka import Producer, Consumer, KafkaException

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
    conf = {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
    }
    c = Consumer(conf)
    c.subscribe(topics)
    return c
