# Architecture

The authenticated FastAPI gateway writes a generated S3 object key, then commits the analysis and a `certificate_uploaded` outbox event in PostgreSQL. The OCR worker starts a separate outbox dispatcher thread that publishes pending events to Kafka; its consumer writes OCR results and an `ocr_completed` event transactionally. The forensics consumer reads the committed OCR result from PostgreSQL, writes optional heatmap to S3, and commits signals and an `analysis_completed` outbox event. The gateway generates reports on demand and checks ownership before status, heatmap or report access. Redis is a fail-open per-user upload limiter, not the job store.

```mermaid
sequenceDiagram
 participant C as Client
 participant A as API
 participant S as S3
 participant D as PostgreSQL
 participant P as OCR dispatcher
 participant K as Kafka
 participant O as OCR worker
 participant F as Forensics worker
 C->>A: POST /analyze + bearer token
 A->>S: put generated key
 A->>D: commit analysis + upload outbox
 A-->>C: 202, analysis_id
 P->>D: lock pending event
 P->>K: publish, wait for acknowledgement
 P->>D: mark sent and commit
 K->>O: certificate_uploaded
 O->>D: commit OCR + ocr_completed outbox
 P->>K: publish ocr_completed
 K->>F: process and persist signals + completion outbox
 C->>A: poll status / retrieve report
```

202 is not completion. Object storage and database are not in a shared transaction; upload then DB failure may leave an orphan. Outbox publication and DB acknowledgement can race, so duplicate delivery is expected. Forensics heatmap upload may precede a failed DB commit, leaving an object. The completion topic has no in-repo consumer. No production high availability, migration framework or independent deployment validation is claimed.
