"""Tests for the authentication that was restored during Day 1 consolidation
(previously entirely absent from app/api.py -- see commit
'Remove authentication entirely for public access')."""


def test_register_creates_account_and_returns_token(client):
    resp = client.post("/auth/register", json={"email": "alice@example.com", "password": "correcthorse1"})
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_register_rejects_short_password(client):
    resp = client.post("/auth/register", json={"email": "alice@example.com", "password": "short"})
    assert resp.status_code == 422  # pydantic min_length violation


def test_register_rejects_duplicate_email(client):
    client.post("/auth/register", json={"email": "bob@example.com", "password": "correcthorse1"})
    resp = client.post("/auth/register", json={"email": "bob@example.com", "password": "differentpass1"})
    assert resp.status_code == 409


def test_login_succeeds_with_correct_credentials(client):
    client.post("/auth/register", json={"email": "carol@example.com", "password": "correcthorse1"})
    resp = client.post("/auth/login", json={"email": "carol@example.com", "password": "correcthorse1"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_fails_with_wrong_password(client):
    client.post("/auth/register", json={"email": "dave@example.com", "password": "correcthorse1"})
    resp = client.post("/auth/login", json={"email": "dave@example.com", "password": "wrongpassword"})
    assert resp.status_code == 401


def test_login_fails_for_unknown_email_with_identical_message(client):
    """Both 'wrong password' and 'no such user' must return the same
    message, so the API can't be used to enumerate registered emails."""
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


def test_me_requires_a_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_rejects_garbage_token(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_me_returns_current_user(client, auth_headers):
    headers = auth_headers(email="erin@example.com")
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "erin@example.com"


def test_analyze_requires_auth(client):
    """This is the core regression test for the auth-removal bug: /analyze
    must reject unauthenticated requests."""
    resp = client.post("/analyze", files={"file": ("cert.png", b"not-a-real-png", "image/png")})
    assert resp.status_code == 401


def test_status_requires_auth(client):
    resp = client.get("/status/some-fake-id")
    assert resp.status_code == 401


def test_report_requires_auth(client):
    resp = client.get("/report/some-fake-id")
    assert resp.status_code == 401


def test_heatmap_requires_auth(client):
    resp = client.get("/heatmap/some-fake-id")
    assert resp.status_code == 401
