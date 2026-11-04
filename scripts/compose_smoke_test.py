#!/usr/bin/env python3
"""End-to-end smoke test, designed to run INSIDE the api-gateway container.

    docker compose exec -T api-gateway python - < scripts/compose_smoke_test.py

WHY IT RUNS IN THE CONTAINER
----------------------------
It needs Pillow to render a real image for OCR to read, and an HTTP client.
Rather than install either on the CI runner, it uses what the application image
already contains: Pillow is a hard dependency (app/ocr.py and app/forensics.py
import PIL), and HTTP is done with stdlib urllib so `requests` is not required.
Text is drawn with ImageFont.load_default(size=34), which is Pillow's bundled
scalable font -- no font files needed, and large enough for tesseract to read
the labels that app/ocr.py's field regexes anchor on.

WHAT IT PROVES
--------------
This is the first check anywhere that the distributed pipeline actually works
end to end, rather than that its parts individually import and unit-test
cleanly. It drives one real request through every hop:

    HTTP upload -> magic-byte validation -> MinIO/S3 write -> Postgres row
      -> Kafka publish -> worker-ocr consumes -> tesseract OCR
      -> Kafka publish -> worker-forensics consumes -> PIL scoring
      -> heatmap PNG back to MinIO -> Postgres row updated
      -> client polls /status and reads /heatmap

If any hop is broken -- wrong bootstrap server, a topic that cannot be created,
MinIO credentials that do not match, a worker that crashes on import, OCR
missing its language data -- this fails, and the unit tests (which fake Kafka
and S3 at the Python-call boundary) would not have caught it.

Deliberately tolerant of STARTUP timing but not of incorrectness: /analyze
may return 503 while object storage is starting. A broker outage leaves a durable pending outbox entry; 202 does not mean the workers finished. A 4xx, a failed analysis, or
empty OCR output is a real failure and is not retried.

Exit 0 = pipeline verified. Exit 1 = something is broken.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")
# Allow storage startup retries and a separate processing deadline; queued
# jobs can wait for Kafka even after the upload returns 202.
ANALYZE_DEADLINE = float(os.environ.get("SMOKE_ANALYZE_DEADLINE", "240"))
STATUS_DEADLINE = float(os.environ.get("SMOKE_STATUS_DEADLINE", "300"))

failures: list[str] = []
checks: list[str] = []


def ok(msg: str) -> None:
    checks.append(msg)
    print(f"  PASS  {msg}")


def bad(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def request(method, path, *, data=None, headers=None, timeout=30):
    """Return (status, body_bytes). Raises nothing on HTTP errors."""
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:                       # connection refused, timeout
        return 0, f"{type(exc).__name__}: {exc}".encode()


def multipart(field_name: str, filename: str, blob: bytes, content_type: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand (urllib has no helper)."""
    boundary = f"----certifakesmoke{uuid.uuid4().hex}"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.write(blob)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


#: Text chosen to match what app/ocr.py's extract_fields() actually looks for.
#: Those regexes are KEYWORD-ANCHORED (`name:`, `date:`, `certificate id`,
#: `institute|university|...`), so a realistic-looking certificate without
#: those labels extracts nothing -- verified, not guessed: an earlier version of
#: this image rendered "This is to certify that / Jane A. Doe" and
#: extract_fields() returned {} because no line began with "Name:".
#: Rendering the labels makes the smoke test verify the field-extraction stage
#: end to end, not merely that tesseract ran.
CERT_LINES = [
    "CERTIFICATE OF COMPLETION",
    "",
    "Name: Jane A Doe",
    "Certificate ID: CF-2026-000123",
    "Date: 2026-09-28",
    "Institution: Global Institute of Technology",
    "",
    "Distributed Systems Engineering",
]


def make_certificate_png() -> bytes:
    """Render a PNG containing real labelled text, so OCR has something to
    extract and extract_fields() has the keywords its regexes anchor on.

    `load_default(size=...)` returns a scalable FreeType font (Pillow >= 10.1;
    this project pins pillow==13.0.0) and needs no font files in the image.
    The size matters: at the old ~11px bitmap default, tesseract read
    "Certificate ID:" as "Cortiticate 10:" and the field regex missed.
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=34)
    except TypeError:                      # Pillow < 10.1: no size parameter
        font = ImageFont.load_default()

    img = Image.new("RGB", (1500, 700), "white")
    d = ImageDraw.Draw(img)
    y = 70
    for line in CERT_LINES:
        if line:
            d.text((90, y), line, fill="black", font=font)
        y += 66
    d.rectangle([30, 30, 1470, 670], outline="black", width=5)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def wait_for_api(deadline: float = 180) -> bool:
    start = time.monotonic()
    last = ""
    while time.monotonic() - start < deadline:
        status, body = request("GET", "/health", timeout=5)
        if status == 200:
            ok(f"GET /health -> 200 {body[:80].decode(errors='replace').strip()}")
            return True
        last = f"status={status} body={body[:120].decode(errors='replace')}"
        time.sleep(3)
    bad(f"API never became ready within {deadline}s (last: {last})")
    return False


def main() -> int:
    print(f"CertiFake compose smoke test -> {BASE}")
    print(f"  analyze deadline {ANALYZE_DEADLINE}s, status deadline {STATUS_DEADLINE}s\n")

    if not wait_for_api():
        print("\nCannot continue without a reachable API.")
        return 1

    # ---------------------------------------------------------------- auth
    email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
    password = "smoke-test-password-123"
    status, body = request(
        "POST", "/auth/register",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if status != 201:
        bad(f"POST /auth/register -> {status} (want 201): {body[:200].decode(errors='replace')}")
        return report()
    token = json.loads(body)["access_token"]
    ok(f"POST /auth/register -> 201, JWT issued for {email}")

    status, body = request("GET", "/auth/me", headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        bad(f"GET /auth/me -> {status} (want 200): {body[:200].decode(errors='replace')}")
    else:
        ok(f"GET /auth/me -> 200 (token accepted, user {json.loads(body).get('id', '?')[:8]}...)")

    # ------------------------------------------------------------ analyze
    png = make_certificate_png()
    ok(f"rendered a {len(png)}-byte PNG containing real text for OCR")
    blob, ctype = multipart("file", "certificate.png", png, "image/png")
    auth = {"Authorization": f"Bearer {token}", "Content-Type": ctype}

    analysis_id = None
    start = time.monotonic()
    attempt = 0
    while time.monotonic() - start < ANALYZE_DEADLINE:
        attempt += 1
        status, body = request("POST", "/analyze", data=blob, headers=auth, timeout=60)
        if status == 202:
            analysis_id = json.loads(body)["analysis_id"]
            ok(f"POST /analyze -> 202 on attempt {attempt}, analysis_id={analysis_id[:12]}...")
            break
        if status == 503:
            # Storage can still be starting; Kafka outages leave events in the outbox.
            elapsed = int(time.monotonic() - start)
            print(f"  ...   /analyze 503 (infra not ready), retrying [{elapsed}s elapsed]")
            time.sleep(5)
            continue
        bad(f"POST /analyze -> {status} (want 202): {body[:300].decode(errors='replace')}")
        return report()
    if analysis_id is None:
        bad(f"POST /analyze never returned 202 within {ANALYZE_DEADLINE}s "
            f"({attempt} attempts, all 503) -- storage never became usable")
        return report()

    # ------------------------------------------------------------- status
    final = None
    start = time.monotonic()
    while time.monotonic() - start < STATUS_DEADLINE:
        status, body = request("GET", f"/status/{analysis_id}",
                               headers={"Authorization": f"Bearer {token}"})
        if status != 200:
            bad(f"GET /status/{analysis_id[:8]}... -> {status}: {body[:200].decode(errors='replace')}")
            return report()
        final = json.loads(body)
        st = final.get("status")
        if st == "completed":
            ok(f"pipeline completed in {int(time.monotonic() - start)}s (status=completed)")
            break
        if st == "failed":
            bad(f"analysis ended FAILED: {json.dumps(final)[:400]}")
            return report()
        time.sleep(4)
    else:
        bad(f"analysis never reached a terminal state within {STATUS_DEADLINE}s "
            f"(last status={final.get('status') if final else '?'}) -- a worker is "
            f"not consuming, or the event was lost between stages")
        return report()

    # --------------------------------------------- the results are real
    ocr_text = final.get("ocr_text") or ""
    if not ocr_text.strip():
        bad("ocr_text is empty -- the OCR worker ran but extracted nothing, so "
            "tesseract is likely missing its eng traineddata in the image")
    else:
        ok(f"ocr_text has {len(ocr_text)} chars of real extracted text")
        # The image literally contains this string; OCR should find it. This is
        # the assertion that distinguishes "the worker ran" from "the worker
        # actually performed OCR".
        if "CERTIFICATE" in ocr_text.upper():
            ok("OCR recovered the rendered text 'CERTIFICATE' from the image")
        else:
            bad(f"OCR text does not contain 'CERTIFICATE' though it was rendered "
                f"into the image; got: {ocr_text[:160]!r}")

    if final.get("authenticity_score") is None and final.get("confidence") is None and final.get("verdict") == "Inconclusive":
        ok("uncalibrated authenticity/confidence are not presented as evidence")
    else:
        bad("result must be inconclusive with no authenticity or confidence score")

    fields = final.get("extracted_fields") or {}
    expected = {"name": "Jane A Doe", "certificate_no": "CF-2026-000123",
                "date": "2026-09-28", "institution": "Global Institute of Technology"}
    if fields == expected:
        ok("four expected field values match the rendered synthetic document")
    else:
        bad(f"OCR fields did not match the synthetic fixture: {fields!r}")

    # A second authenticated account must not read another user's data.
    other = f"smoke-other-{uuid.uuid4().hex[:10]}@example.com"
    status, body = request("POST", "/auth/register", data=json.dumps({"email": other, "password": password}).encode(),
                           headers={"Content-Type": "application/json"})
    if status != 201:
        bad("Could not register second user to check ownership")
    else:
        other_token = json.loads(body)["access_token"]
        for endpoint in ("status", "heatmap", "report"):
            code, _ = request("GET", f"/{endpoint}/{analysis_id}", headers={"Authorization": f"Bearer {other_token}"})
            if code == 404:
                ok(f"other user denied access to {endpoint}")
            else:
                bad(f"other user received {code} for {endpoint}; expected 404")

    # ------------------------------------------------------- artifacts
    for path, label in ((f"/heatmap/{analysis_id}", "heatmap PNG"),
                        (f"/report/{analysis_id}", "report")):
        status, body = request("GET", path, headers={"Authorization": f"Bearer {token}"})
        if status != 200:
            bad(f"GET {path} -> {status} (want 200): {body[:160].decode(errors='replace')}")
        elif len(body) < 100:
            bad(f"GET {path} -> 200 but only {len(body)} bytes; {label} looks empty")
        else:
            ok(f"GET {path} -> 200, {len(body)} bytes ({label} served to owner)")

    return report()


def report() -> int:
    print()
    print(f"{len(checks)} check(s) passed, {len(failures)} failed")
    if failures:
        print("\nSMOKE TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSMOKE TEST PASSED: the full distributed pipeline works end to end "
          "against the services configured for this run. Container use must be verified separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
