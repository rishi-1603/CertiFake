"""Shared pytest fixtures for the CertiFake backend test suite.

Design notes:
  - Uses a throwaway on-disk SQLite DB (not the dev sqlite file, not
    Postgres) so tests never depend on a running Postgres/Kafka/MinIO
    stack, and never touch real developer data. Set DATABASE_URL *before*
    importing anything under app/, since app/models.py reads it at
    import time.
  - Kafka and S3/MinIO calls are monkeypatched to fast in-memory fakes for
    the majority of tests (which are testing endpoint/auth/ownership
    logic, not infrastructure). Two dedicated tests
    (test_failure_modes.py) intentionally do NOT patch these, to prove the
    503-on-outage behavior added during Day 1 consolidation actually
    works against the real (unreachable, in this sandbox) client code
    paths.
"""
import os
import tempfile

import fakeredis
import pytest

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-production"

from fastapi.testclient import TestClient  # noqa: E402

import app.api as api_module  # noqa: E402
import app.rate_limit as rate_limit_module  # noqa: E402
from app.models import Base, engine  # noqa: E402


class FakeProducer:
    """Stands in for confluent_kafka.Producer without needing a broker."""

    def __init__(self):
        self.published = []


@pytest.fixture(autouse=True)
def _reset_db():
    """Give every test a clean set of tables (SQLite, fast to recreate)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """No real Redis runs in the test sandbox, and rate-limit counters must
    never leak between tests. A fresh fakeredis instance per test gives
    every test isolated, deterministic counters without needing a live
    Redis server -- same approach already used in the sibling DevTrack
    project's test suite for the same reason."""
    fake_client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(rate_limit_module, "_client", fake_client)
    yield fake_client


@pytest.fixture
def fake_kafka(monkeypatch):
    """Patch app.api.produce_event so tests default to a healthy broker
    (returns True == delivered) without any network call."""
    calls = []

    def _fake_produce_event(producer, topic, key, value_dict):
        calls.append((topic, key, value_dict))
        return True

    monkeypatch.setattr(api_module, "produce_event", _fake_produce_event)
    return calls


@pytest.fixture
def fake_storage(monkeypatch, tmp_path):
    """Patch app.api's upload/download so tests default to healthy storage
    backed by a local temp dir instead of a real MinIO/S3 endpoint."""
    store = {}

    def _fake_upload(file_key, data):
        store[file_key] = data
        return f"fake://{file_key}"

    def _fake_download(file_key):
        from app.s3_utils import StorageObjectNotFoundError

        if file_key not in store:
            raise StorageObjectNotFoundError(file_key)
        return store[file_key]

    monkeypatch.setattr(api_module, "upload_file_bytes", _fake_upload)
    monkeypatch.setattr(api_module, "download_file_bytes", _fake_download)
    return store


@pytest.fixture
def client(fake_kafka, fake_storage):
    return TestClient(api_module.app)


TEST_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
    b"\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def auth_headers(client):
    """Register a user and return an Authorization header for them."""

    def _make(email="user1@example.com", password="hunter22222"):
        resp = client.post("/auth/register", json={"email": email, "password": password})
        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _make
