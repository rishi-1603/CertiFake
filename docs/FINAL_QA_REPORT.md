# CertiFake — Days 8–10 final QA report

**Verification date:** 2026-10-12 (Asia/Calcutta / UTC evidence timestamps)  
**Verified main commit:** `220e28a1`  
**Repository:** https://github.com/rishi-1603/CertiFake

This report separates the public API deployment from the full distributed stack.
That distinction matters: a healthy gateway is not evidence that Kafka, MinIO,
OCR, and the forensics worker are reachable.

## Result

**Overall: PARTIAL**

The codebase, unit suite, frontend build, deployment configuration, container
build, and CI Compose pipeline pass. The public Vercel frontend and Render API
are reachable; live authentication, upload validation, graceful storage failure,
and metrics pass. The public Render environment does not have reachable
MinIO/Kafka, so a successful live document-processing workflow is not claimed.

## Evidence matrix

| Area | Result | Evidence |
|---|---|---|
| Core workflow in full stack | **PASS in CI** | Main/PR CI Compose smoke passed; historical run [36613263530](https://github.com/rishi-1603/CertiFake/actions/runs/36613263530) recorded the real upload → MinIO → Postgres → Kafka → OCR → forensics → report/heatmap journey |
| Public frontend | **PASS** | `https://certi-fake.vercel.app` returned HTTP 200; current bundle calls the Render gateway; frontend build and lint passed |
| Public API liveness/docs | **PASS** | `https://certifake.onrender.com/health`, `/docs`, `/openapi.json`, and `/metrics` returned HTTP 200 |
| Live auth | **PASS** | Live register → login → `/auth/me` passed with a throwaway account |
| Malicious/malformed upload | **PASS** | A text file renamed `.png` returned 400 from magic-byte validation; unauthenticated upload returned 401 |
| Live successful upload | **UNVERIFIED / deployment PARTIAL** | A valid rendered certificate returned the designed 503 storage-unavailable response because MinIO/Kafka are not reachable in the Render environment |
| Critical automated tests | **PASS** | **89 passed**, 78% production coverage in local run; CI test job passed |
| Security/dependencies | **PASS** | Backend `pip-audit`: `No known vulnerabilities found`; frontend `npm audit --omit=dev`: 0 vulnerabilities; tracked-file secret scan clean |
| CI/CD | **PASS** | Main run [38230228321](https://github.com/rishi-1603/CertiFake/actions/runs/38230228321): test, frontend-build, config-validation, Docker build, Compose smoke, and Docker publish all passed |
| Kubernetes | **UNVERIFIED** | `kubeconform`/cross-file checks pass in CI, but no live cluster exists and manifests have never been applied |
| README/architecture/screenshots | **PASS** | README now distinguishes live Render degradation from CI full-stack evidence; `docs/architecture.svg` and genuine UI captures are included |
| Second verification | **PASS for controls / PARTIAL for live full pipeline** | CI rerun after documentation/frontend changes; live auth, validation, 503, and metrics rechecked |

## Security and reliability findings verified

- Authentication and owner isolation are enforced on analysis, status, report,
  and heatmap endpoints.
- Uploads require an allowed declared type, matching extension, matching
  libmagic signature, non-empty content, and a size within the configured limit.
  Filenames are reduced to a basename before storage-key construction.
- Kafka consumers use bounded retry, jitter, manual commits, dead-letter topics,
  and stage idempotency. A failed DLQ publish does not commit the source offset.
- Worker/API metrics are real HTTP surfaces and the live gateway exposes
  `certifake_analyze_*` outcome metrics.
- Errors exposed to callers are safe messages; document text and raw storage
  paths are not logged by the request handlers.
- Malware scanning is still not implemented and is explicitly listed as a
  limitation; type validation is not malware detection.

## Deployment truth

- **Vercel frontend:** https://certi-fake.vercel.app
- **Render API:** https://certifake.onrender.com
- **Full-stack evidence:** CI Compose smoke, not the current public Render API.
- **Kubernetes:** schema/cross-file validation only; no live cluster evidence.

## Artifacts

- [Architecture diagram](architecture.svg)
- [Live Vercel capture](screenshots/certifake-live-frontend.png)
- [Current source capture](screenshots/certifake-local-frontend.png)
- [Frontend verification notes](../frontend/README.md)
