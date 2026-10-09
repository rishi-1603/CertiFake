"""Day-5 deep pass: the token matrix, ownership parity, and input boundaries.

What is *not* here, on purpose: the cases already covered by test_auth.py,
test_upload_validation.py, test_ownership.py and test_rate_limit.py. This file
adds the checks those suites leave out — a forged signature, a token that has
expired, an id that never existed compared against somebody else's id, an empty
file, a missing part in the multipart body, and the exact password-length
boundary — plus two tests that pin defects this audit found and that are still
open. Those two are marked `xfail(strict=True)`: they document the bug, keep the
suite green for CI, and turn into a **failure** the moment somebody fixes the
code without deleting the marker, so the marker cannot be forgotten.
"""
import time

import jwt
import pytest

from app.config import settings

# --------------------------------------------------------------------------
# token matrix
# --------------------------------------------------------------------------

def test_no_authorization_header_is_401(client):
    assert client.get("/status/anything").status_code == 401


def test_garbage_token_is_401(client):
    assert client.get("/status/anything", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_token_signed_with_the_wrong_key_is_401(client):
    forged = jwt.encode({"sub": "someone", "exp": int(time.time()) + 600}, "a-different-secret-key", algorithm="HS256")
    assert client.get("/status/anything", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_expired_token_is_401(client, auth_headers):
    """An expired token must be refused, not accepted because the signature is valid."""
    token = auth_headers()["Authorization"].split()[1]
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    payload["exp"] = int(time.time()) - 5
    expired = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    assert client.get("/status/anything", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_valid_token_reaches_the_handler(client, auth_headers):
    """Control case: the same route with a good token gets past auth (404 for the
    unknown id), proving the four 401s above are auth failures and not routing."""
    assert client.get("/status/does-not-exist", headers=auth_headers()).status_code == 404


# --------------------------------------------------------------------------
# ownership parity: "not yours" and "does not exist" must be indistinguishable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("route", ["status", "report", "heatmap"])
def test_missing_analysis_and_other_users_analysis_are_indistinguishable(client, auth_headers, route):
    """A valid id belonging to somebody else and an id that never existed must
    return the same status AND the same body. Anything else lets an attacker
    probe which analysis ids exist."""
    other = auth_headers("owner@example.com")
    intruder = auth_headers("intruder@example.com")

    resp = client.post("/analyze", headers=other,
                       files={"file": ("c.png", _PNG, "image/png")})
    assert resp.status_code == 202
    victim_id = resp.json()["analysis_id"]

    ours = client.get(f"/{route}/{victim_id}", headers=intruder)
    nonexistent = client.get(f"/{route}/no-such-id", headers=intruder)
    assert ours.status_code == nonexistent.status_code == 404
    assert ours.json() == nonexistent.json()


# --------------------------------------------------------------------------
# input boundaries
# --------------------------------------------------------------------------

def test_password_of_exactly_eight_characters_is_accepted(client):
    """min_length is 8, so 8 must pass and 7 must fail — the boundary itself,
    not a value comfortably inside or outside it."""
    assert client.post("/auth/register", json={"email": "eight@example.com", "password": "12345678"}).status_code == 201
    assert client.post("/auth/register", json={"email": "seven@example.com", "password": "1234567"}).status_code == 422


def test_missing_file_part_is_rejected(client, auth_headers):
    resp = client.post("/analyze", headers=auth_headers(), files={})
    assert resp.status_code == 422


def test_empty_file_is_rejected(client, auth_headers):
    """Zero bytes cannot be a valid document; the sniffer must say so rather
    than letting an empty object into the pipeline."""
    resp = client.post("/analyze", headers=auth_headers(), files={"file": ("empty.png", b"", "image/png")})
    assert resp.status_code == 400


def test_file_whose_extension_lies_about_its_content_is_rejected(client, auth_headers):
    """A .png that contains text: the declared content type and the real bytes
    disagree, and the bytes win."""
    resp = client.post("/analyze", headers=auth_headers(),
                       files={"file": ("certificate.png", b"just some text, no magic bytes", "image/png")})
    assert resp.status_code == 400


def test_unknown_analysis_id_is_404_not_500(client, auth_headers):
    assert client.get("/status/zzzz", headers=auth_headers()).status_code == 404


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
    b"\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


# --------------------------------------------------------------------------
# defects found by the Day-3/Day-4 audit, pinned here while they are open
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="D4-1: no reachable score gives 'Needs Review' or 'Likely Genuine'")
def test_clean_certificate_is_not_labelled_likely_fake():
    """The README documents three verdicts: >=80 Likely Genuine, 55-79 Needs
    Review, <55 Likely Fake (forensics.py). A tidy, text-rich certificate with
    no tampering signals is currently scored 50 (OpenCV path) or 45-50 (fallback)
    and therefore called 'Likely Fake' with zero suspicious signals — the worst
    possible output, because the user is told the document is fake and given no
    reason. Measured on Day 4 against a rendered 1600x1130 certificate.
    """
    from app import forensics

    good_text = ("Certificate of Completion. This is to certify that Rishi Kumar has successfully "
                 "completed the programme at Andhra University, issued 12 May 2026.")
    score, signals, _ = forensics._fallback_score(good_text, "application/pdf")
    assert not signals, "a clean document should raise no signals"
    assert score >= 55, (
        f"a clean certificate scored {score}, which lands in 'Likely Fake' "
        f"even though the README defines 55-79 as 'Needs Review'"
    )


@pytest.mark.xfail(strict=True, reason="D3-3: /auth/login has no rate limiting")
def test_login_is_rate_limited(client):
    """/analyze is limited to 10/min; /auth/login is unlimited, which makes the
    login form the cheapest place to guess passwords. DevTrack and Repay-Master
    both limit their login endpoints, so this is an inconsistency inside the
    portfolio as well as a security gap."""
    client.post("/auth/register", json={"email": "bruteforce@example.com", "password": "correcthorse1"})
    codes = [client.post("/auth/login", json={"email": "bruteforce@example.com", "password": "wrong-password"})
             .status_code for _ in range(25)]
    assert 429 in codes, f"25 failed logins produced {set(codes)} — no rate limit fired"
