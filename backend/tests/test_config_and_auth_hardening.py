"""Tests for the Day-7 configuration and auth hardening.

Three findings are pinned here:

* **S8** — `DATABASE_URL` no longer falls back to a file-backed SQLite, so a
  container started without it fails instead of silently losing data.
* **S14** — `secret_key` no longer defaults to a string published in this
  repository, so a forgotten `SECRET_KEY` cannot produce JWTs that anyone can
  forge.
* **S2/S6** — the JWT library is PyJWT (python-jose is gone), at a version that
  handles a malicious deeply-nested token as a decode error rather than letting
  a `RecursionError` escape.

The first two have to be tested in subprocesses. Both values are read at import
time, so "the app refuses to start without them" is not observable from inside a
process where they are already set — and `tests/conftest.py` sets both before
importing anything under `app/`, deliberately, so the rest of the suite can run.
"""
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

from app.config import MIN_SECRET_KEY_BYTES

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _import_in_subprocess(module: str, extra_env: dict | None = None):
    """Import `module` in a clean interpreter with the named vars removed."""
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SECRET_KEY")}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestMissingConfigurationFailsLoudly:
    def test_missing_database_url_refuses_to_start(self):
        """S8: the old default was `sqlite:///./certifake.db`.

        A production container missing DATABASE_URL used to start happily on a
        file-backed SQLite inside the container: no error, no data persistence
        across restarts, and a silent divergence from the Postgres the rest of
        the stack was writing to.
        """
        result = _import_in_subprocess(
            "app.models", extra_env={"SECRET_KEY": "not-used-by-this-import"}
        )
        assert result.returncode != 0, "importing app.models without DATABASE_URL must fail"
        assert "DATABASE_URL" in result.stderr
        # The message has to say what to do, not just what is wrong: this fires
        # at container startup, possibly in a log nobody is watching live.
        assert "Refusing to start" in result.stderr
        assert "postgresql://" in result.stderr

    def test_missing_secret_key_refuses_to_start(self):
        """S14: the old default was `change_me_to_a_long_random_secret`.

        That string is committed to a public repository, so a deployment that
        forgot SECRET_KEY would still start and still issue tokens — signed with
        a key anyone could read, meaning anyone could mint a valid token for any
        user. Failing at import is the only safe behaviour.
        """
        result = _import_in_subprocess(
            "app.config", extra_env={"DATABASE_URL": "sqlite:///./not-used.db"}
        )
        assert result.returncode != 0, "importing app.config without SECRET_KEY must fail"
        assert "secret_key" in result.stderr

    def test_neither_field_can_be_given_a_default_again(self):
        """Guard against the fallbacks being reintroduced by a well-meaning edit.

        Deliberately a structural assertion, not a substring search: both files
        quote the old insecure values in comments explaining why they were
        removed, so "the literal string is absent" would be both wrong and
        brittle. What actually matters is that the declaration carries no
        default.
        """
        config_source = (BACKEND_DIR / "app" / "config.py").read_text()
        assert re.search(r"^\s*secret_key:\s*str\s*=", config_source, re.M) is None, (
            "secret_key must be declared without a default"
        )
        assert re.search(r"^\s*secret_key:\s*str\s*$", config_source, re.M), (
            "secret_key should still be a required str field"
        )

        models_source = (BACKEND_DIR / "app" / "models.py").read_text()
        assert re.search(r'getenv\(\s*"DATABASE_URL"\s*\)', models_source), (
            "DATABASE_URL must be read with a single-argument getenv"
        )
        assert re.search(r'getenv\(\s*"DATABASE_URL"\s*,', models_source) is None, (
            "DATABASE_URL must not have a fallback value"
        )


class TestJwtLibraryMigration:
    def test_the_jwt_module_in_use_is_pyjwt_not_jose(self):
        """S2: python-jose is unmaintained and PYSEC-2025-185 has no fix."""
        import app.auth as auth_module

        assert auth_module.jwt is jwt
        # PyJWT's exception base class is what the 401 handler catches; jose's
        # JWTError no longer exists in this codebase.
        assert auth_module.PyJWTError is jwt.exceptions.PyJWTError
        assert not hasattr(auth_module, "JWTError")
        assert "jose" not in sys.modules

    def test_expired_token_is_still_rejected(self, client):
        """The migration must not silently drop expiry verification.

        PyJWT and python-jose both verify `exp` by default, but that is exactly
        the kind of default worth pinning when swapping a library on the auth
        path -- so this asserts it twice: once at the library level, and once
        through the real `require_user` dependency on a real endpoint.
        """
        from app.config import settings

        expired = jwt.encode(
            {
                "sub": "someone",
                "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            },
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(expired, settings.secret_key, algorithms=[settings.jwt_algorithm])

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert response.status_code == 401, response.text


class TestSigningKeyStrength:
    """S15: a *missing* secret was already fatal; a *weak* one now is too.

    HS256 uses the secret directly as an HMAC key, and RFC 7518 3.2 requires at
    least the hash output length. Below that, a signature can be brute-forced
    offline from a single captured token -- and unlike a missing secret, a short
    one does not announce itself: everything appears to work.

    Note the deliberate asymmetry with the sibling projects, which enforce this
    only when APP_ENV=production: CertiFake is not deployed anywhere, so the
    unconditional rule costs nothing here and is the correct one the day it is.
    """

    _DB = {"DATABASE_URL": "sqlite:///./not-used-by-this-import.db"}

    def test_short_secret_key_refuses_to_start(self):
        result = _import_in_subprocess("app.config", extra_env={**self._DB, "SECRET_KEY": "short"})
        assert result.returncode != 0, "a short signing key must not start the app"
        assert "secret_key" in result.stderr
        assert str(MIN_SECRET_KEY_BYTES) in result.stderr
        assert "secrets.token_hex" in result.stderr

    @pytest.mark.parametrize("length", [31, 1, 0])
    def test_everything_below_the_minimum_is_refused(self, length):
        result = _import_in_subprocess(
            "app.config", extra_env={**self._DB, "SECRET_KEY": "a" * length}
        )
        assert result.returncode != 0

    def test_key_at_the_minimum_is_accepted(self):
        result = _import_in_subprocess(
            "app.config", extra_env={**self._DB, "SECRET_KEY": "a" * MIN_SECRET_KEY_BYTES}
        )
        assert result.returncode == 0, result.stderr

    def test_a_generated_key_is_accepted(self):
        """The command the error message tells you to run must actually work."""
        import secrets as _secrets

        result = _import_in_subprocess(
            "app.config", extra_env={**self._DB, "SECRET_KEY": _secrets.token_hex(32)}
        )
        assert result.returncode == 0, result.stderr

    def test_threshold_is_the_rfc_minimum_not_an_arbitrary_number(self):
        assert MIN_SECRET_KEY_BYTES == 32


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class TestMaliciousTokenHandling:
    def test_deeply_nested_forged_token_is_a_401_not_a_500(self, client):
        """Regression test for the PyJWT payload-recursion advisory (S6).

        A token whose payload segment is valid JSON nested ~20k deep made
        `json.loads` raise `RecursionError`, which is not a `ValueError`, so it
        escaped every documented PyJWT error type and surfaced as an unhandled
        exception on an auth path — an unauthenticated 500 per request. PyJWT
        2.15.0 converts it to `DecodeError`. This asserts the behaviour this app
        actually exposes: 401, no stack trace, and the process still serving the
        next request.
        """
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url(b"[" * 20_000 + b"]" * 20_000)
        forged = f"{header}.{payload}.{_forged_signature()}"

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401, response.text
        assert "RecursionError" not in response.text

        # And the app is still healthy afterwards -- the point of the advisory
        # was resource/exception damage per request, not a single bad response.
        assert client.get("/health").status_code == 200


def _forged_signature() -> str:
    return _b64url(b"forged-signature-not-valid")
