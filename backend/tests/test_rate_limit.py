"""Tests for /analyze's per-user rate limiting (app/rate_limit.py)."""
import redis

import app.rate_limit as rate_limit_module
from app.config import settings
from tests.conftest import TEST_PNG_BYTES


def _upload(client, headers):
    return client.post(
        "/analyze",
        files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )


def test_requests_within_limit_are_allowed(client, auth_headers):
    headers = auth_headers()
    for _ in range(settings.analyze_rate_limit_per_minute):
        resp = _upload(client, headers)
        assert resp.status_code == 202


def test_requests_over_limit_return_429_with_retry_after(client, auth_headers):
    headers = auth_headers()
    for _ in range(settings.analyze_rate_limit_per_minute):
        assert _upload(client, headers).status_code == 202

    resp = _upload(client, headers)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) > 0


def test_rate_limit_is_scoped_per_user_not_global(client, auth_headers):
    """User A hitting the limit must not block user B."""
    headers_a = auth_headers(email="a@example.com")
    headers_b = auth_headers(email="b@example.com")

    for _ in range(settings.analyze_rate_limit_per_minute):
        assert _upload(client, headers_a).status_code == 202
    assert _upload(client, headers_a).status_code == 429

    # B has made zero requests so far -- must still be allowed.
    assert _upload(client, headers_b).status_code == 202


def test_rate_limit_fails_open_when_redis_is_unreachable(client, auth_headers, monkeypatch):
    """If Redis itself errors, /analyze must still work -- a deliberate
    availability-over-throttling trade-off, not a bug. Simulated with a
    broken client (not just fakeredis) so this actually exercises the
    except redis.RedisError branch, not just the happy path."""

    class BrokenClient:
        def incr(self, *a, **kw):
            raise redis.ConnectionError("simulated outage")

    monkeypatch.setattr(rate_limit_module, "_client", BrokenClient())

    headers = auth_headers()
    resp = _upload(client, headers)
    assert resp.status_code == 202


def test_other_endpoints_are_not_rate_limited(client, auth_headers):
    """Only /analyze does expensive downstream work (S3 + Postgres + Kafka
    fan-out to two workers) -- health/auth/status must not be throttled."""
    headers = auth_headers()
    for _ in range(settings.analyze_rate_limit_per_minute + 5):
        resp = client.get("/auth/me", headers=headers)
        assert resp.status_code == 200
