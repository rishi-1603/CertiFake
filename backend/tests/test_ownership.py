"""Tests that one authenticated user cannot see another user's analyses.

This is a stricter guarantee than existed even before the auth-removal
regression: the pre-removal code only checked "is there a valid token",
never "does this token's user own this specific analysis ID".
"""
from tests.conftest import TEST_PNG_BYTES


def _upload(client, headers):
    resp = client.post(
        "/analyze",
        files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["analysis_id"]


def test_user_cannot_view_another_users_status(client, auth_headers):
    owner_headers = auth_headers(email="owner@example.com")
    other_headers = auth_headers(email="other@example.com")

    analysis_id = _upload(client, owner_headers)

    own_view = client.get(f"/status/{analysis_id}", headers=owner_headers)
    assert own_view.status_code == 200

    other_view = client.get(f"/status/{analysis_id}", headers=other_headers)
    assert other_view.status_code == 404  # not 403 -- see app/api.py docstring


def test_user_cannot_view_another_users_report(client, auth_headers):
    owner_headers = auth_headers(email="owner2@example.com")
    other_headers = auth_headers(email="other2@example.com")

    analysis_id = _upload(client, owner_headers)

    resp = client.get(f"/report/{analysis_id}", headers=other_headers)
    assert resp.status_code == 404


def test_user_cannot_view_another_users_heatmap(client, auth_headers):
    owner_headers = auth_headers(email="owner3@example.com")
    other_headers = auth_headers(email="other3@example.com")

    analysis_id = _upload(client, owner_headers)

    resp = client.get(f"/heatmap/{analysis_id}", headers=other_headers)
    assert resp.status_code == 404


def test_nonexistent_analysis_id_returns_404(client, auth_headers):
    headers = auth_headers(email="lonely@example.com")
    resp = client.get("/status/does-not-exist", headers=headers)
    assert resp.status_code == 404
