# CertiFake repository audit

Audit base: `984264f9ca896687eaadb61b8741bdb115f886e0` · 4 November 2026. This inventory distinguishes inspection and local unit-test results from a real-infrastructure run. It is not a security certification.

## Current architecture and inventory

`backend/app/api.py` exposes FastAPI authentication, upload, status, heatmap, report, liveness and Prometheus metrics; `auth.py` handles bcrypt and signed JWTs; `models.py` creates the users/analyses schema at import. `security.py` checks size, extension, MIME and libmagic; `s3_utils.py` wraps S3 storage; `kafka_utils.py` publishes and creates manual-commit consumers; `consumer.py` runs retry/DLQ logic; `workers/ocr.py` and `workers/forensics.py` process events; `ocr.py`, `forensics.py` and `report.py` perform extraction, heuristic image analysis and PDF reporting. `rate_limit.py` uses Redis as a transient limiter. The React UI is in `frontend/`; Compose, Dockerfile, Kubernetes manifests, monitoring, GitHub Actions, 12 test modules and `scripts/compose_smoke_test.py` provide operational and test paths. There are no migration scripts or Alembic configuration.

| Component | Baseline classification | Evidence and limitation |
|---|---|---|
| Unit/API suite | WORKING locally, mocked integrations | 89 passed on Python 3.13; SQLite, fakes and unavailable-service tests, not a real-infrastructure test. |
| React frontend | WORKING build | `npm ci --ignore-scripts`, `npm run lint -- --deny-warnings`, `npm run build` succeeded. |
| Auth/ownership | PARTIALLY WORKING | Tests cover JWT and IDOR; access tokens only; no refresh/revocation; no login brute-force limit. |
| Upload | PARTIALLY WORKING | Auth, magic, size and owner checks exist, but `await file.read()` buffers without a pre-read limit; images are not fully decoded or pixel-limited; matching extension only distinguishes PDF vs image. |
| PostgreSQL | PARTIALLY WORKING | SQLAlchemy models and serialized `create_all`; no evolving-schema migrations. Existing ownership FK is nullable for legacy rows. |
| Kafka | BROKEN in failure windows | OCR saves output before publishing the next event, ignores failed `produce_event`, and treats saved text as a duplicate; a failed publish or crash can permanently strand the analysis. Failed DLQ publication does not commit that message, but the loop keeps polling and a later commit can advance over it. Commit acknowledgement is asynchronous. |
| Object storage | PARTIALLY WORKING | MinIO/S3 adapter exists; DB insert failure after successful upload can orphan the object. Cross-service transaction is not atomic. |
| OCR | PARTIALLY WORKING | Tesseract implementation exists; institution regex has alternation/grouping errors and label extraction can cross lines. PDF conversion renders all pages before using page one. No timeout or evaluated accuracy corpus. |
| Forensics | PARTIALLY WORKING / misleading | JPEG recompression difference and image/OCR heuristics are real, but arbitrary score, 'Likely Fake/Genuine' and score/100 'confidence' are uncalibrated and can imply proof of authenticity. PDF takes fallback path, not a pixel analysis. |
| Reporting | PARTIALLY WORKING | Owner-scoped PDF produced after completion; file cleanup is not exception-safe; display must reflect forensic limitations. |
| Redis | PARTIALLY WORKING | Rate limiting fails open when unavailable; no durable job state in Redis. |
| Docker / CI | NOT VERIFIED locally | Docker CLI/daemon unavailable in this environment. Historical Compose CI evidence is in `FINAL_QA_REPORT.md`; that evidence is not this audit's verification. |
| Kubernetes | NOT VERIFIED / optional | Manifests exist, no cluster available; not required for this local project release. |
| Benchmarks and OCR corpus | MISSING | No independently measured representative corpus, accuracy or load benchmark in this audit. |

## Highest-priority risks and repair direction

1. Prevent lost messages from OCR → forensic stage; use a database-backed transactional outbox only with migration-safe rollout and failure-window tests. An acknowledged publish can still be repeated: never call this exactly-once.
2. Prevent a later offset from skipping a message whose DLQ publication failed; use a fail-closed consumer restart and synchronous confirmed offset commits.
3. Bounded file reads, safe generated names, decode limits and format correspondence; never equate signature checking with malware detection.
4. Replace authenticity and confidence claims with clearly uncalibrated anomaly signals; retain backwards-compatible fields only where their semantics are honest.
5. Correct first-page PDF processing and field extraction; bound OCR time/resources.
6. Add real-service end-to-end and failure-injection checks, distinguish them from mocks; validate owner isolation.
7. Document exact limitations, schema evolution gap, deployment and operational procedures. Avoid promising production security, accuracy or performance.

## Local environment

Docker and Podman unavailable. PostgreSQL 17, Redis 8, Tesseract 5.5, and a locally extracted MinIO server were started for isolated test use; Kafka 3.9.1 in single-node KRaft mode was separately downloaded and its Apache SHA-512 checked. Local native processes are not proof that Docker Compose works. CI and live public deployment must be checked separately.
