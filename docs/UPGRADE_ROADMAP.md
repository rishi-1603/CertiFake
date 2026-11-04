# Seven-day engineering roadmap

This is a prioritized seven-workday plan, not a claim of seven days of elapsed work or autonomous background execution. Status and evidence are tracked in the delivery report.

| Day | Goal | Acceptance evidence |
|---|---|---|
| 1 | Inventory, baseline, model critical failure windows — **COMPLETE** | [Day 1 audit](DAY_1_AUDIT_REPORT.md), baseline and targeted security/reliability regressions; exact-commit CI and deployment verified separately. |
| 2 | Harden upload, authentication boundary and forensic semantics | Bounded upload/decode, deterministic validation tests, no unsupported authenticity/confidence claim; document remaining gaps. |
| 3 | Make DB-to-broker handoff recoverable | Atomic outbox writes for API and OCR transitions; dispatcher locking, retries, restart and duplicate-publish tests; document at-least-once limits. |
| 4 | Harden consumers and processing | Safe offset decisions, DLQ and retries, idempotent stages, OCR time and PDF page limits, field-extraction regression tests. |
| 5 | Real infrastructure integration and failure injection | PostgreSQL/Kafka/MinIO/Redis/Tesseract upload → OCR → analysis → report; ownership and broker outage/recovery tests; report environment and gaps. |
| 6 | Packaging, operations and security | Compose and CI review, health/readiness, secret handling, threat model, migration and storage-reconciliation plan, lint and regressions. |
| 7 | Evidence and engineering handoff | Re-run checks, independently review failure windows, document architecture/trade-offs, create reproducible benchmarks if measured, and write evidence-based resume/interview material. |

Never mark the complete platform production-ready based on unit tests alone. Reassess scope and ordering if a critical issue prevents safe execution. Phases not completed must remain explicitly open.
