"""Environment helpers for the modules that must NOT import app.config.

WHY THIS EXISTS (final-audit Day 3, defect D1-3)
------------------------------------------------
app/s3_utils.py and app/kafka_utils.py read their configuration straight from
os.environ instead of going through app/config.py, because the worker containers
deliberately do not set SECRET_KEY and must not import Settings (which requires
it). That is the right call -- but it produced a failure that was hard to
diagnose: `os.getenv("MINIO_ENDPOINT", "http://localhost:9000")` returns the
EMPTY STRING when a .env file defines the key and leaves the value blank -- the
default only applies when the key is absent. The empty string then reached
boto3, which raised `ValueError: Invalid endpoint:` *at import time*, killing the
whole gateway process with a message that never named the setting that was wrong.

A blank value is a mistake worth failing on -- silently substituting the local
default would hide a misconfiguration in a container, where localhost is the
wrong host and the symptom would be a confusing 503 later. So this raises
instead, and the message says which variable and what to do about it.
"""
from __future__ import annotations

import os


def env_or_default(name: str, default: str) -> str:
    """Return an environment value, defaulting only when the key is ABSENT.

    Absent  -> `default` (the documented local-development value).
    Set     -> the stripped value.
    Blank   -> RuntimeError naming the variable, because "set but empty" is a
               mistake that used to crash the process from inside a library.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise RuntimeError(
            f"{name} is set but empty. Remove the key to use the built-in default "
            f"({default!r}), or give it a real value."
        )
    return value
