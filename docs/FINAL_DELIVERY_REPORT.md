# CertiFake upgrade — delivery evidence

## Executive summary

Baseline commit `984264f9ca896687eaadb61b8741bdb115f886e0` had 89 local tests and a working native happy path, but OCR fields included labels/wrong institution, messages could be stranded between DB and Kafka, failed DLQ handling could advance a later offset, uploads had unbounded handler reads and the app displayed uncalibrated authenticity/confidence claims. This upgrade addresses these issues; it is **not** a completed seven-day production certification.

## Verification matrix

| Feature | Status | Evidence / boundary |
|---|---|---|
| Registration/login/owner isolation | PASS selected paths | Unit/API suite and native two-user status/heatmap/report 404 checks. Refresh/revocation/login rate limiting missing. |
| Upload | PASS selected paths | Magic/type/extension, generated keys, bounded handler reads, image decode/pixel checks; malformed/oversize tests. PDF safety, reverse proxy cap and malware scanning remain. |
| OCR | PASS small synthetic cases | Four expected fields in native end-to-end sample; three synthetic orientation variants via `scripts/evaluate_ocr.py`. Not real-world accuracy. |
| Forensics/report | PARTIAL | Real image heatmap and PDF retrieved. Result inconclusive; no validated fraud detection, PDF pixel analysis or issuer verification. |
| DB/Kafka handoff | PASS one outage | Native single-broker stop→upload→pending outbox→restart→completion; unit rollback/retry tests. Not exactly-once or complete distributed fault testing. |
| Dead letter and offsets | PARTIAL | Fake-client tests cover failed DLQ, terminal failure, synchronous commit and no later-message polling. No real crash/rebalance/DLQ integration test. |
| PostgreSQL | PARTIAL | Native Postgres used for full path and new outbox table creation. No schema migrations or multi-replica stress test. |
| Redis | PARTIAL | Native Redis on happy path; fail-open behavior unit-tested. Persistence is in Postgres. |
| Docker/Compose | BLOCKED locally | No Docker daemon; native processes are not Compose. Existing workflow includes a Compose smoke; exact new commit must pass CI. |
| Live hosted stack | NOT VERIFIED | Public hosted environment was not used for this upgrade. |
| Security scan | PASS at scan time | `pip-audit -r backend/requirements.txt` reported 39 advisory entries for Pillow 12.3.0, then zero after pinning 13.0.0. `npm audit --omit=dev` reported zero. Neither is a security certificate. |

## Tests and measurements

- `cd backend && python -m pytest -q --cov=app --cov-report=term-missing`: **104 passed**, 83% statement coverage on Python 3.13.16; coverage varies by environment and does not measure correctness. Most tests use SQLite/fakes. Seven warnings include deprecations/resource warnings in that run.
- `ruff check app tests`, frontend lint/build, image-registry resolution and config consistency passed locally. Registry check reported three nonfatal mutable `latest` warnings for Kubernetes image references.
- Native smoke: 15 checks passed, including correct four synthetic field values and owner isolation, using PostgreSQL 17, Redis 8, Kafka 3.9.1 in KRaft mode, MinIO and Tesseract 5.5.0. No Docker/ZooKeeper was involved in this native run.
- OCR synthetic evaluation: 12/12 field instances over three variants of one template; an earlier 12.3.0 environment before rotation fallback scored 8/12. This small nonrepresentative exercise is not a general accuracy claim.
- Throughput, latency percentiles and load performance: **NOT MEASURED**. A single smoke duration is not a benchmark.

## Key changes

`backend/app/outbox.py`, `models.py`, `api.py`, `consumer.py` and workers implement atomic DB/outbox transitions, retries and sync commit/stop-on-failure. `security.py` bounds and validates uploads; `auth.py` requires JWT claims and rejects overlong bcrypt inputs; `ocr.py` fixes labelled values, first-page PDF and rotation fallback; `forensics.py` signals remain heuristic while workers/API/frontend/report no longer represent scores as calibrated authenticity or confidence. `scripts/compose_smoke_test.py` adds four-field and two-user checks; `scripts/evaluate_ocr.py` documents tiny synthetic OCR evaluation. Documentation and resume/interview evidence are linked from README.

## Remaining priorities

**Critical:** production-safe PDF/malware sandbox and proxy body caps; robust migration strategy; prevent/repair orphan S3 objects and handle DB commit ambiguity; exhaustive real Kafka crash/rebalance/DLQ and multi-replica tests; full-stack deployment verification; no default infra credentials for production. **High:** login throttling and token revocation/recovery, reprocessing/versioned jobs, independent labelled OCR/forensic evaluation, operational readiness and alerting, concurrency and load benchmarks. **Optional:** stronger observability and UI accessibility review.

See [architecture](ARCHITECTURE.md), [security](SECURITY.md), [testing](TESTING.md), [seven-day roadmap](UPGRADE_ROADMAP.md), [strict scorecard](QUALITY_SCORECARD.md), [resume](../RESUME_NOTES.md) and [interview pack](INTERVIEW_PREP.md).
