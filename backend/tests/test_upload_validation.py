"""Tests for app/security.py's upload validation, wired into /analyze.

Before Day 1 consolidation, app/api.py's /analyze had NO validation of any
kind -- any bytes, any declared content type, any size would be accepted
and pushed straight into the pipeline.
"""
from tests.conftest import TEST_PNG_BYTES


def test_rejects_disallowed_declared_content_type(client, auth_headers):
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("payload.exe", b"MZ\x90\x00fake-exe-bytes", "application/x-msdownload")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_rejects_mismatched_magic_bytes(client, auth_headers):
    """A file that CLAIMS to be a PNG (correct extension, correct declared
    Content-Type) but whose actual bytes are plain text must be rejected --
    this is exactly the "renamed malicious file" attack the magic-byte
    check exists to stop."""
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("cert.png", b"This is not actually a PNG file at all.", "image/png")},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "do not match" in resp.json()["detail"]


def test_rejects_extension_mismatched_with_declared_type(client, auth_headers):
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("cert.pdf", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_rejects_empty_file(client, auth_headers):
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("cert.png", b"", "image/png")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_path_traversal_filename_is_sanitized(client, auth_headers, fake_storage):
    """A filename like '../../etc/passwd.png' must never be used verbatim
    to build the storage key -- only its basename may be used."""
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("../../../etc/passwd.png", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 202
    stored_keys = list(fake_storage.keys())
    assert len(stored_keys) == 1
    assert ".." not in stored_keys[0]
    assert stored_keys[0].endswith("/passwd.png")


def test_accepts_valid_png(client, auth_headers):
    headers = auth_headers()
    resp = client.post(
        "/analyze",
        files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "analyzing"
    assert "analysis_id" in body
