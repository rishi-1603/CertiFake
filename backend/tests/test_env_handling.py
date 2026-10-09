"""Regression tests for the blank-environment-variable crash (defect D1-3).

On 2026-10-02, a .env that defined MINIO_ENDPOINT but left the value blank took
the whole gateway down at import time with `ValueError: Invalid endpoint:` from
inside botocore -- a message that never named the setting. `os.getenv(name, default)`
returns "" when the key is present and empty (the default only applies when the
key is absent), and "" reached boto3.

The unit tests below pin the helper's contract. The subprocess test pins the
thing that actually mattered: what a developer sees when they make this mistake.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.env_utils import env_or_default

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_absent_key_uses_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CF_TEST_ABSENT", raising=False)
    assert env_or_default("CF_TEST_ABSENT", "fallback") == "fallback"


def test_set_value_is_used_and_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_TEST_SET", "  http://storage:9000  ")
    assert env_or_default("CF_TEST_SET", "fallback") == "http://storage:9000"


def test_blank_value_raises_a_message_naming_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIO_ENDPOINT", "   ")
    with pytest.raises(RuntimeError) as excinfo:
        env_or_default("MINIO_ENDPOINT", "http://localhost:9000")
    message = str(excinfo.value)
    assert "MINIO_ENDPOINT is set but empty" in message
    assert "http://localhost:9000" in message, "the message must state the default it would have used"


def test_blank_minio_endpoint_fails_with_our_message_not_botocore() -> None:
    """The original symptom, in a subprocess so the import happens exactly once."""
    env = {**os.environ, "MINIO_ENDPOINT": ""}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.s3_utils"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert proc.returncode != 0, "a blank MINIO_ENDPOINT must stop the process"
    assert "MINIO_ENDPOINT is set but empty" in proc.stderr
    assert "Invalid endpoint" not in proc.stderr, "the old, unnamed botocore failure must be gone"


def test_blank_kafka_bootstrap_fails_the_same_way() -> None:
    env = {**os.environ, "KAFKA_BOOTSTRAP_SERVERS": ""}
    proc = subprocess.run(
        [sys.executable, "-c", "import app.kafka_utils"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert proc.returncode != 0
    assert "KAFKA_BOOTSTRAP_SERVERS is set but empty" in proc.stderr
