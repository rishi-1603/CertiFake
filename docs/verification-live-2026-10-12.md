# CertiFake live verification — 2026-10-12

Targets:

- Frontend: https://certi-fake.vercel.app
- API: https://certifake.onrender.com

Observed:

- frontend HTTP 200; deployed bundle configured for the Render API
- `/health`, `/docs`, `/openapi.json`, `/metrics`: HTTP 200
- register/login/`/auth/me`: 201 / 200 / 200
- valid rendered certificate upload: controlled HTTP 503 because the Render
  service has no reachable MinIO/Kafka
- text file renamed to `.png`: HTTP 400, magic-byte mismatch
- upload without bearer token: HTTP 401
- Prometheus output recorded `certifake_analyze_requests_total{outcome="storage_unavailable"}`

The full successful upload → OCR → forensics → report/heatmap path is verified by
the CI Compose smoke job and is not claimed as a successful workflow on this
current API-only Render deployment.
