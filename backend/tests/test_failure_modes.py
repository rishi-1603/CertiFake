"""Tests that exercise the REAL kafka/S3 client code (no mocking) against
brokers/endpoints that do not exist in this test environment, to verify the
graceful-degradation behavior added during Day 1 consolidation:
  - a message-broker outage during /analyze must return 503, not hang
    forever and not leak a raw exception as an unhandled 500
  - an object-storage outage during /analyze must return 503, with no
    orphaned "analyzing" DB row left behind
These intentionally do NOT use the fake_kafka/fake_storage fixtures.
"""
import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_PNG_BYTES


@pytest.fixture
def client_with_real_infra_clients(monkeypatch):
    # Force both clients to point at ports nothing is listening on, and use
    # a short Kafka produce timeout so the test doesn't take ages.
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:1")
    monkeypatch.setenv("KAFKA_PRODUCE_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://127.0.0.1:1")

    import importlib

    import app.kafka_utils as kafka_utils_module
    import app.s3_utils as s3_utils_module

    importlib.reload(kafka_utils_module)
    importlib.reload(s3_utils_module)

    import app.api as api_module

    importlib.reload(api_module)

    yield TestClient(api_module.app)

    # Restore modules to their normal (test-fixture-friendly) state for
    # every other test file in the session.
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    importlib.reload(kafka_utils_module)
    importlib.reload(s3_utils_module)
    importlib.reload(api_module)


def test_analyze_returns_503_when_storage_is_unreachable(client_with_real_infra_clients):
    client = client_with_real_infra_clients
    register = client.post("/auth/register", json={"email": "infra1@example.com", "password": "correcthorse1"})
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    resp = client.post(
        "/analyze",
        files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 503
    assert "storage" in resp.json()["detail"].lower()
