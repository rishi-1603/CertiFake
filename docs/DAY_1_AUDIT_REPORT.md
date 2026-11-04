# Day 1 — repository audit and baseline

**Baseline:** GitHub `main` at `0f329e91467992a775b789ad1c02cf77c59eb660`, inspected 4 November 2026. [Baseline CI](https://github.com/rishi-1603/CertiFake/actions/runs/41857320966) passed, including Docker/Compose smoke. This is historical evidence for that commit, not proof of a currently hosted deployment. Day 1 is an engineering scope, not a claim that seven calendar days have elapsed.

## Inventory and execution path

- API: `backend/app/api.py`, `auth.py`, `schemas.py`; nine routes for liveness, metrics, register, login, current user, upload, status, heatmap and report.
- Persistence: `models.py` contains users, analyses and outbox; schema starts via `create_all`, not a migration framework. `s3_utils.py` stores source images and heatmaps.
- Processing: `outbox.py` dispatches committed events; `consumer.py`, `kafka_utils.py`, and `workers/ocr.py` / `workers/forensics.py` implement Kafka handling and database state. The dispatcher runs in the OCR worker process.
- Analysis: `ocr.py` extracts four labelled fields; `forensics.py` calculates heuristic signals and a JPEG recompression-difference heatmap; `report.py` generates a PDF. Results do not prove authenticity or fraud.
- Supporting components: `rate_limit.py` uses Redis; `metrics.py` exposes instrumentation. React UI is under `frontend/`; Compose, Kubernetes examples, Prometheus, Dockerfile and GitHub Actions are configuration rather than proof of production readiness.
- Tests and utilities: 13 baseline Python test modules; `scripts/compose_smoke_test.py`, `scripts/evaluate_ocr.py` and config/image checks. No Alembic/migrations directory, refresh-token service or replay endpoint exists.

## Reproduced Day 1 findings and immediate repairs

1. **High — worker trusted object references from Kafka.** With a synthetic SQLite database and fake storage, a forged event for owner A referencing an owner B object was passed directly to the OCR worker. The worker downloaded the supplied key and recorded its extracted value under owner A. Reproduction requires an attacker with event-publish capability and access to suitable object keys; it is not evidence of an unauthenticated public-API exploit.
   - **Repair:** both workers validate `analysis_id`, owner-scoped canonical/legacy object key and content type against the database before accessing storage. For analyses created with an upload outbox, the relevant event must additionally match its committed stage outbox row. Invalid event errors do not mark a victim's job failed.
   - **Verification:** unit cases cover both worker paths, cross-owner and same-owner forged references, required stage presence, valid generated/legacy paths, and invalid-event state handling. This does not authenticate Kafka, stop someone who can publish a perfectly matching event, or prove protection from all compromised-broker attacks. Legacy rows lacking an owner now fail closed; recovery/migration requires an explicit plan.
2. **High — Redis fixed-window key could lose its TTL.** A simulated connection failure between `INCR` and `EXPIRE` left a persistent counter; subsequent requests were denied indefinitely while `Retry-After` misleadingly showed one second.
   - **Repair:** use atomic Redis `MULTI/EXEC` for `INCR` and `EXPIRE NX`, preserving an existing window while repairing counters with no TTL. The existing fail-open behavior on Redis outage is unchanged.
   - **Verification:** regression tests plus a real local Redis 8.0.2 check for normal limiting, stale TTL repair and no window extension. No multi-node Redis/production failover test was run.

## Verification

| Check | Result | Scope |
|---|---|---|
| Baseline before fixes | 104 passed; 83% statement coverage | Local Python 3.13, predominantly SQLite/fakes; not production reliability. |
| Day 1 fixes | 114 passed; 83% statement coverage | Same environment; seven runtime/deprecation/resource warnings. Coverage is not proof of correctness. |
| Backend lint / configuration | PASS | Ruff and cross-file configuration check. |
| Frontend | PASS | `npm ci --ignore-scripts`, lint and production build; `npm audit --omit=dev` reported zero vulnerabilities at scan time. |
| OCR | PASS on tiny fixture | Four values on each of three synthetic variants of the same template; not a real-world accuracy claim. |
| Redis | PASS selected behavior | Real local Redis 8.0.2 plus fake-client regressions. |
| Docker / Kafka / Postgres for Day 1 patch | NOT RUN locally | Docker unavailable in this sandbox. Baseline commit's full Compose smoke passed on GitHub; the new patch needs its own CI run. |

The Python 3.13 isolated install initially failed to compile `confluent-kafka` because `librdkafka/rdkafka.h` was missing. Installing `librdkafka-dev` resolved the failure. The project's CI and Docker image target Python 3.11, so this is not evidence that their published wheel needs compiling.

## Prioritized backlog

- **P0:** Broker network isolation, Kafka authentication/ACLs and TLS; the event checks above are defense in depth, not producer identity or a substitute for authorization. Protect S3 credentials and use least-privilege policies.
- **P0:** Malicious PDF/resource isolation, malware scanning, request-size enforcement before multipart parsing and storage quotas. No real-document processing deployment should be called secure until evaluated.
- **P1:** Schema migrations; determine remediation for legacy null-owner rows; S3/database orphan reconciliation and commit-ambiguity recovery.
- **P1:** Real broker duplicate, crash, rebalance, DLQ and multi-worker tests; bounded outbox backlog/retention; auditable reprocessing and replay.
- **P1:** Login throttling, token revocation/recovery, access-token storage and secret rotation; note Redis upload limiter intentionally fails open.
- **P2:** Representative labelled OCR and forensic evaluation, PDF pixel analysis, readiness/alerting, accessible UI review, reproducible load tests and latency percentiles.

## Day 1 exit

Inventory, baseline, architecture/failure-window analysis and prioritized backlog are complete. The two high-priority findings above received targeted fixes. Day 1 completion does **not** mean Days 2–7 or every acceptance criterion are finished. Verify the exact new commit's CI and deployment independently.
