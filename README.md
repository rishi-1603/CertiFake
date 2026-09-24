# CertiFake — Certificate Forensics API

CertiFake analyzes uploaded certificate images/PDFs for signs of tampering
or fabrication, using OCR text extraction and pixel-level forensic
heuristics (not a trained ML classifier — see "How scoring works" below for
exactly what is and isn't implemented).

## Status: mid-rebuild (Day 1-2 of a 7-day hardening pass)

This repository previously contained **three separate, overlapping
implementations** of the same idea (a Streamlit app, a synchronous FastAPI
demo backend, and an async Kafka/S3-backed FastAPI gateway), which had
drifted out of sync with each other, and one of them had authentication
**completely removed** in a prior commit. This section documents what is
true today, not what any of the old READMEs (or code comments) claimed.

**As of Day 1, the repo has been consolidated down to one backend:**
`backend/app/api.py` (a FastAPI + Postgres + Kafka + S3/MinIO service),
which is also what `docker-compose.yml` and `k8s/deployment.yaml` were
already pointed at before this cleanup — they just didn't match the code
running behind them. The Streamlit app and the standalone synchronous demo
backend have been deleted; their forensic logic was already just calling
into `backend/app/forensics.py` and `backend/app/ocr.py`, which are shared
and unaffected.

### What is implemented and verified today

- **Authentication (restored):** `/auth/register`, `/auth/login`,
  `/auth/me` — real Postgres-backed users, bcrypt password hashing, JWT
  bearer tokens. `/analyze`, `/status/{id}`, `/report/{id}`, and
  `/heatmap/{id}` all require a valid token.
  - This closes a real regression: a previous commit
    ("Remove authentication entirely for public access") had stripped auth
    from the old demo backend's `/analyze` and `/report/{id}` endpoints
    entirely, leaving them open to the public.
  - The consolidated version also adds a guarantee the *original* auth
    never had: analyses are scoped per-owner. Two different logged-in
    users cannot see each other's results, even by guessing/enumerating a
    valid analysis ID (verified by `backend/tests/test_ownership.py`).
- **Upload validation:** declared Content-Type allow-list, file extension
  cross-check, and real magic-byte sniffing via `libmagic` (so a file that
  is *renamed* to look like a PNG but isn't one gets rejected, not just a
  file with a "wrong" declared type) — see `backend/app/security.py`.
  Filenames are sanitized to their basename before being used to build a
  storage key, so a path-traversal filename like `../../etc/passwd.png`
  cannot escape the intended object-storage prefix.
- **OCR:** Tesseract + `pdf2image`, extracts raw text and regex-parses a
  handful of fields (certificate number, name, date, institution) —
  `backend/app/ocr.py`.
- **Forensic scoring (real, but modest — see below):** `backend/app/forensics.py`.
- **Distributed pipeline:** `/analyze` uploads to MinIO/S3, writes a
  Postgres row, and publishes a Kafka event; `worker-ocr` and
  `worker-forensics` consume events and update the row asynchronously.
  Clients poll `/status/{id}`.
- **Graceful degradation on infra outages:** if Kafka or MinIO/S3 is
  unreachable, `/analyze` returns a `503` with a clear message instead of
  hanging indefinitely or leaking a raw stack trace. Verified with a real
  (unreachable-endpoint) test in `backend/tests/test_failure_modes.py`, not
  just asserted in a comment.
- **Rate limiting on `/analyze` (Day 2):** Redis-backed, fixed-window
  (10 requests/60s per authenticated user, `app/rate_limit.py`) — the file
  the previous line's Kafka/S3 pipeline fans out to two worker processes
  is expensive enough that one scripted client hammering it could flood
  the whole pipeline, not just slow down the API. Returns `429` with a
  `Retry-After` header when exceeded. Deliberately fails *open* (allows
  the request) if Redis itself is unreachable — documented as a
  considered availability-over-throttling trade-off in the module
  docstring, not an oversight.
  - Audit finding fixed in passing: the `redis` package and a running
    `redis` container already existed in `requirements.txt` and
    `docker-compose.yml` before Day 2, with no application code ever
    calling either of them. This feature is the first real use of both.
- **Automated tests:** 29 pytest tests covering auth, cross-user ownership
  isolation, upload validation, the storage-outage failure path, and
  rate-limiting (including a real Redis-outage simulation) (`backend/tests/`).

### What is NOT implemented yet (tracked for later days of this pass, not claimed as done)

- Kafka consumer **retry/DLQ** handling — a worker crash mid-message today
  relies on Kafka's own consumer-group rebalance/redelivery; there is no
  explicit dead-letter queue or backoff policy yet.
- Prometheus metrics are scraped per `monitoring/prometheus.yml`, but the
  API does not yet expose a `/metrics` endpoint with real application
  metrics (this is a config file pointing at nothing yet).
- Real end-to-end integration test against actual Kafka/Postgres/MinIO
  containers (today's test suite mocks those integrations at the
  Python-call boundary, or in one case points the real client at a
  guaranteed-unreachable address to test the failure path — it does not
  spin up the full docker-compose stack).
- Malware scanning of uploaded files (only type/format validation exists).
- `k8s/deployment.yaml` still references a `your-dockerhub-user/...`
  placeholder image and has not been deployed anywhere real.

### How scoring works (exactly, not aspirationally)

`score_document()` starts at a baseline of 50 and applies these adjustments:

| Signal | Condition | Score impact |
|---|---|---|
| Low-resolution upload | image smaller than 300x300 after downscaling | −10 |
| Compression inconsistency | mean Error-Level-Analysis diff > 15 | −18 |
| Unusual edge pattern | Canny edge density < 2% of pixels | −6 |
| Weak OCR text | extracted text under 30 characters | −15 |
| Missing expected certificate keywords | none of "certificate/degree/diploma/issued/university/completion" found in OCR text | −7 |
| PDF bonus | uploaded file is a PDF | +5 |

Final score is clamped to 0–100. Verdict thresholds (from
`backend/app/workers/forensics.py`): **≥80 = Likely Genuine, 55–79 = Needs
Review, <55 = Likely Fake.**

If OpenCV is unavailable at runtime, `forensics.py` falls back to a
text-only heuristic (`_fallback_score`) using only the OCR-keyword and
text-length checks — this fallback path exists in the code but is not the
normal path (`CV_AVAILABLE=True` in the shipped Docker image and in this
project's dev environment).

This is **heuristic image forensics**, not a trained classifier and not a
guarantee of authenticity — see the Disclaimer below. Some claims from an
earlier version of this README (QR code verification, EXIF metadata
forensics, noise-pattern analysis, texture analysis, layout analysis, "12+
forensic checks") described the retired Streamlit app, not this backend,
and have been removed rather than left inaccurate.

## Architecture

```
Client (React frontend, or curl/Postman)
  |
  |  POST /auth/register, /auth/login   -> JWT
  |  POST /analyze (Bearer token)       -> 202 Accepted, analysis_id
  v
FastAPI gateway (app/api.py)
  |         \
  |          -> S3/MinIO: raw file bytes stored at {user_id}/{analysis_id}/{filename}
  |          -> Postgres: CertificateAnalysis row (status=analyzing)
  |          -> Kafka topic "certificate_uploaded"
  v
worker-ocr (app/workers/ocr.py)
  |  downloads file, runs Tesseract OCR + field extraction
  |  writes ocr_text/extracted_fields to Postgres
  |  publishes Kafka topic "ocr_completed"
  v
worker-forensics (app/workers/forensics.py)
  |  downloads file, runs score_document() (OpenCV ELA + edge + OCR heuristics)
  |  uploads heatmap PNG to S3/MinIO
  |  writes score/verdict/signals/status=completed to Postgres

Client polls GET /status/{analysis_id} (Bearer token, ownership-checked)
Client downloads GET /report/{analysis_id} (PDF) and GET /heatmap/{analysis_id} (PNG)
```

Every service in `docker-compose.yml` (`api-gateway`, `worker-ocr`,
`worker-forensics`) builds from the same `backend/Dockerfile` and runs a
different entrypoint command against the same codebase — there is exactly
one Python package (`backend/app/`) now, not three.

## Running locally

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# System dependencies (Ubuntu/Debian) for OCR + PDF handling:
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng poppler-utils libmagic1

cp .env.example .env   # then set a real SECRET_KEY (see the comment in that file)

# Quickest way to try the API without Kafka/Postgres/MinIO running: SQLite
# is used automatically if DATABASE_URL is unset, but Kafka/S3 calls will
# fail fast (503) without those services -- for the full pipeline, use
# docker-compose instead:
uvicorn app.api:app --reload
```

### Full stack (Postgres + Kafka + MinIO + workers)

```bash
# from the repo root
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
docker-compose up --build
```

This starts Postgres, Kafka (+ Zookeeper), MinIO, the API gateway, both
workers, and Prometheus/Grafana (Prometheus currently has nothing real to
scrape — see "What is NOT implemented yet" above).

### Tests

```bash
cd backend
pip install -r requirements.txt
pytest -v
```

24/24 tests currently pass. They use a throwaway SQLite database and mock
Kafka/S3 calls at the Python function boundary for most tests; one test
suite (`test_failure_modes.py`) points the *real* Kafka/S3 client code at
an intentionally unreachable address to verify the 503 failure-handling
behavior actually works, not just that it's mocked to look like it works.

## Frontend

`frontend/` is a small React (Vite) app with a login/signup form and a
drag-and-drop upload UI that polls `/status/{id}` and renders the result,
heatmap, and a PDF report download.

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` if the backend isn't at `http://127.0.0.1:8000`.

## Disclaimer

This tool provides a **probabilistic, heuristic forensic assessment**
based on a small number of image-analysis signals (see the scoring table
above) — it is not a machine-learning classifier trained on labeled
genuine/fake certificates, and it is **not** a substitute for official
verification by the issuing institution or a professional forensic
document examiner.
