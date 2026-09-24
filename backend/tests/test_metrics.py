"""GET /metrics -- the Prometheus scrape endpoint added in Day 3.

monitoring/prometheus.yml already targeted "api-gateway:8000" on this
exact path before any code implemented it; these tests are what actually
prove the endpoint (and the domain metrics it exposes) is real.
"""
from app.metrics import analyze_requests_total


def test_metrics_endpoint_exposes_prometheus_text_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # prometheus_client's ASGI app serves the standard exposition format,
    # not JSON -- assert the shape rather than exact byte content since
    # process/gc default collectors vary by platform.
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "# HELP" in body
    assert "# TYPE" in body


def test_metrics_endpoint_does_not_require_auth(client):
    # Prometheus itself has no way to send a JWT; the scrape endpoint must
    # be reachable without one (same posture as GET /health).
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_successful_analyze_increments_accepted_counter(client, auth_headers, tmp_path):
    headers = auth_headers()
    before = analyze_requests_total.labels(outcome="accepted")._value.get()

    from tests.conftest import TEST_PNG_BYTES

    resp = client.post(
        "/analyze",
        headers=headers,
        files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 202, resp.text

    after = analyze_requests_total.labels(outcome="accepted")._value.get()
    assert after == before + 1

    # And the counter is actually visible on the scrape endpoint, labeled
    # correctly -- not just incremented in memory.
    metrics_body = client.get("/metrics").text
    assert 'certifake_analyze_requests_total{outcome="accepted"}' in metrics_body


def test_rate_limited_analyze_increments_rate_limited_counter(client, auth_headers, monkeypatch):
    import app.api as api_module

    headers = auth_headers()
    # Force the rate limiter to reject regardless of configured threshold,
    # so this test doesn't depend on (or slowly reproduce) the real
    # per-minute limit from settings.
    monkeypatch.setattr(api_module, "check_rate_limit", lambda *a, **k: (False, 42))

    before = analyze_requests_total.labels(outcome="rate_limited")._value.get()
    resp = client.post(
        "/analyze",
        headers=headers,
        files={"file": ("cert.png", b"not-checked-because-rate-limited", "image/png")},
    )
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "42"

    after = analyze_requests_total.labels(outcome="rate_limited")._value.get()
    assert after == before + 1
