# Technical interview preparation

Use these as prompts to understand and explain the actual repository, not as pre-written claims about personal contribution.

| Question | Tests | Strong answer should cover | Weak answer | Component |
|---|---|---|---|---|
| Trace upload to report. | Architecture | S3 write, DB/outbox commit, dispatcher, Kafka, OCR, second event, forensics, owner polling/report. | “FastAPI calls AI.” | `api.py`, `outbox.py`, workers |
| Why asynchronous? | Trade-offs | OCR is slow; 202 and polling decouple request from processing; adds failure and ops complexity. | “Kafka makes it scalable.” | `api.py`, workers |
| Why Kafka? | Messaging | Durable broker/consumer groups, replay and asynchronous stage decoupling; operational cost and at-least-once duplicates. | “It guarantees exactly once.” | `kafka_utils.py`, `consumer.py` |
| Why Postgres? | Database | Durable transactions, constraints, indexed ownership, row locks; SQL consistency doesn't extend to S3/Kafka. | “Because it is fast.” | `models.py`, `outbox.py` |
| Why MinIO? | Storage | S3-compatible object bytes vs relational metadata; not an atomic DB transaction. | “It is a database.” | `s3_utils.py` |
| Service boundaries? | Design | API, OCR/dispatcher and forensics processes plus infrastructure; they share Python image. | “Every container is a microservice.” | Compose |
| What is validated on upload? | API/security | Byte cap after multipart parsing, MIME/extension/magic, image verification and dimensions, generated key; PDF/malware/proxy limitations. | “MIME means safe.” | `security.py`, `api.py` |
| How is auth implemented? | Security | Bcrypt, signed JWT with required claims and expiration, owner lookup; no refresh/revocation/login throttle. | “JWT never expires.” | `auth.py` |
| How is ownership enforced? | IDOR | DB analysis owner ID compared to authenticated user; 404 for non-owner status/heatmap/report. | “UUIDs are secret.” | `_get_owned_analysis_or_404` |
| Where are transactions? | Correctness | Analysis/outbox, OCR/outbox, forensics/outbox; S3 upload and broker ack outside atomic DB scope. | “Everything commits atomically.” | `api.py`, workers |
| What if Kafka is down on upload? | Recovery | 202 after DB outbox commit, pending until OCR dispatcher publishes; verify broker-stop test and failure limits. | “Request always returns 503.” | `outbox.py` |
| What if worker crashes? | Offsets | No auto-commit, offset after stage/DLQ; crash before commit can redeliver; row lock/committed state guard duplicate work. | “No event is ever duplicated.” | `consumer.py`, workers |
| What if publish succeeds and DB ack fails? | Outbox | Pending row can republish; idempotence and downstream checks, no exactly-once claim. | “That cannot happen.” | `outbox.py` |
| How do retries and DLQ work? | Failure | Three local attempts with jitter, DLQ ack then terminal state then sync offset commit; stop when DLQ/state fails to avoid later offset skipping. | “Retry forever.” | `consumer.py` |
| Can you replay a DLQ? | Operations | Original parsed event for supported messages and error type in DLQ; malformed content only digest/length; no built-in safe replay/versioning. | “Press replay.” | `kafka_utils.py` |
| Which fields and how? | OCR | Labelled name, certificate ID, date, institution; single-line regex after Tesseract; absent fields remain absent. | “It extracts every certificate.” | `ocr.py` |
| How does rotation work? | Signal design | Fewer than two fields triggers two orientations; rank by detected labels then text length; no guarantee. | “We perfectly deskew all scans.” | `ocr.py` |
| Explain heatmap. | Forensics | Resized image recompressed JPEG; absolute difference visualized, not proven edit or fraud. PDF fallback has no image heatmap. | “Red proves forgery.” | `forensics.py` |
| False positives and negatives? | Skepticism | JPEG quality, scans, fonts, resizing, image texture and noisy OCR; no labelled calibration. | “Model is 99% accurate.” | `FORENSIC_ENGINE.md` |
| Can the platform prove authenticity? | Judgment | No; result inconclusive. Verify with issuer or official registry. | “Yes, score above 80.” | `api.py` |
| Schema/indexes? | SQL | Users, analyses FK/owner index, outbox FK and unique analysis/topic, pending sent index; no migrations. | “No database constraints.” | `models.py` |
| Concurrency? | Transactions | Row lock serializes same analysis on Postgres; outbox uses skip-locked; lock held during expensive work is a throughput risk. | “SQLite tests prove multi-node safety.” | workers, `outbox.py` |
| What tests are real services? | Evidence | Native single broker, PG, MinIO, Redis, OCR smoke and one outage/restart; unit/API mostly SQLite/fakes; Compose on new code unverified locally. | “Every test is end-to-end.” | `TESTING.md` |
| What about benchmarks? | Measurement | No load/percentile results; synthetic 3-document OCR set is a smoke, not accuracy estimate. | “It handles thousands per second.” | `BENCHMARKS.md` |
