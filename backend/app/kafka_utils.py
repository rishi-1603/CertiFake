import os
import json
from confluent_kafka import Producer, Consumer

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

def get_kafka_producer():
    conf = {'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS}
    return Producer(conf)

def produce_event(producer, topic, key, value_dict):
    producer.produce(
        topic, 
        key=str(key).encode('utf-8'), 
        value=json.dumps(value_dict).encode('utf-8')
    )
    producer.flush()

def get_kafka_consumer(group_id, topics):
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': group_id,
        'auto.offset.reset': 'earliest'
    }
    c = Consumer(conf)
    c.subscribe(topics)
    return c
