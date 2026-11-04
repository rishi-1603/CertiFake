# CertiFake — resume-ready evidence

**Title:** CertiFake — Asynchronous Certificate Analysis

**Stack:** Python, FastAPI, PostgreSQL, Kafka, MinIO/S3, Redis, Tesseract, Docker Compose, Pytest

**Repository:** https://github.com/rishi-1603/CertiFake

Suggested bullets; use only if you can explain the code and actually performed or co-developed the work:

- Built an authenticated FastAPI document-analysis pipeline backed by PostgreSQL and S3-compatible storage, with owner-scoped status, heatmap and report endpoints.
- Implemented Tesseract field extraction and explainable image-difference signals with an explicit inconclusive verdict; evaluated four fields on three synthetic variants without claiming real-world accuracy.
- Added a transactional outbox and manual Kafka offset handling for retryable processing; verified a broker-stop/restart scenario and ran 104 automated tests locally.

## Metric evidence

- **104 tests:** `cd backend && python -m pytest -q --cov=app --cov-report=term-missing`; Python 3.13.16, isolated SQLite/fakes and selected unavailable-service checks; 4 Nov 2026. Test count is not a reliability or coverage guarantee.
- **Three synthetic variants, four fields:** `python scripts/evaluate_ocr.py`; same base template with upright/90°/3° rotations; Tesseract 5.5.0 and Pillow 13.0.0 on 4 Nov 2026. Do not generalize to production accuracy.
- **Broker stop/restart:** isolated native PostgreSQL 17, Kafka 3.9.1 KRaft, MinIO, Redis 8 and Tesseract; one upload accepted while broker down, then completed after restart. One scenario does not establish exactly-once delivery or availability percentage.

Do not claim a production deployment, independent forensic validation, throughput, latency percentiles, user count, or measured fraud-detection accuracy. A collaborator should not present AI-assisted work as wholly self-authored; be ready to explain your own contribution.
