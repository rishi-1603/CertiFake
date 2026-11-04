# Strict engineering scorecard — local upgrade review

Subjective assessment of the checked scope, not a ranking, certification or external hiring outcome. Scores are deliberately limited by missing production validation.

| Category | Weight | Score | Evidence and remaining gap |
|---|---:|---:|---|
| Core functionality | 15 | 11 | Native full path completed; PDF and edge-case coverage limited. |
| Backend architecture | 10 | 7 | Clear processing boundaries and transactional outbox; import-time setup and no complete state machine. |
| Database engineering | 10 | 5 | Real Postgres and transactional handoffs; no migrations, nullable legacy owner and no extensive concurrency tests. |
| Distributed reliability | 10 | 6 | Broker-stop recovery and unit fault checks; no multi-broker, rebalance, exhaustive crash or replay testing. |
| Security | 10 | 5 | Ownership, upload controls and patched dependency; no malware scanner, auth throttling, revocation, production TLS or pentest. |
| OCR and evaluation | 10 | 5 | Four fields, rotation fallback and tiny synthetic exercise; no representative corpus or calibrated confidence. |
| Forensic quality | 10 | 4 | Explainable signals and inconclusive verdict; no labelled validation, PDF pixel analysis or defensible fraud score. |
| Automated testing | 10 | 6 | 104 unit/API tests plus one native full-path smoke and outage case; mocks dominate and many failures are untested. |
| Docker and CI | 5 | 2 | Existing CI/config, updated smoke; no local Docker daemon or verified new-commit CI at assessment time. |
| Observability | 3 | 1 | Metrics and diagnostic guidance exist; no robust tracing or readiness/alerting. |
| Documentation | 4 | 3 | Current architecture, limits and operational docs; some historical files remain timestamped historical artifacts. |
| Resume/interview | 3 | 2 | Evidence-based bullets and implementation-specific questions; no hiring outcome claimed. |
| **Total** | **100** | **57** | Work remains before a production or high-assurance claim. |
