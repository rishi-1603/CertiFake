# CertiFake — Resume & LinkedIn Bullet Points

*Added Day 7 in the same format as the siblings' notes; every figure measured.
Test count and coverage come from the CI `test` job, which gained `--cov=app`
on Day 7 — before that no percentage existed and none was quoted, which was
the honest position at the time. Coverage is measured on production code only
(`.coveragerc` omits the test directory); see the table and the note below for
what the 78% does and does not mean.*

## Resume bullet points (pick 2-3 based on space)

- Built **CertiFake**, a distributed certificate-forgery detector: uploads flow
  HTTP → S3/MinIO → Postgres → Kafka → an OCR worker (tesseract) → a forensics
  worker, with results, heatmaps and PDF reports served back through the API.
  Ten-service Docker Compose stack whose boot is asserted in CI.

- Consolidated three drifted implementations of the same idea (a Streamlit app,
  a synchronous demo backend, and this async gateway — one of which had
  authentication *removed* in a prior commit) into one backend, one image
  serving three workloads with different commands, and matching k8s manifests.

- Made CI boot the real stack and drive one genuine request through every hop:
  `/analyze` 202 on the first attempt, pipeline complete in 12 s, OCR text
  recovered from a rendered PNG, a forensics score, four extracted fields, and
  a 51 kB heatmap plus PDF report read back out of MinIO. That run is also what
  caught ZooKeeper dying on cgroup-v2 hosts and taking the whole event backbone
  with it.

- Replaced a deleted, CVE-shipping MinIO image (`minio/minio`, removed from
  Docker Hub 2026-09-11) with a digest-pinned Chainguard build, and added a CI
  check that resolves **every** image reference — including Dockerfile `FROM`
  lines — against its registry, which is the check that would have caught the
  deletion on the day it happened.

## One-liner (LinkedIn / portfolio card)

CertiFake — a Kafka-backed certificate-forensics pipeline (OCR + pixel-level
heuristics) in FastAPI, with a 10-container compose stack that CI actually
boots and drives end to end.

## Numbers, and where they come from

| Claim | Value | Source |
|---|---|---|
| pytest cases | 76 | CI `test` job; `def test_` count matches |
| Coverage (production code) | 78% | CI `--cov=app` with `.coveragerc` omitting tests, added Day 7 |
| Compose services | 10 | postgres, redis, zookeeper, kafka, minio, prometheus, grafana, api-gateway, worker-ocr, worker-forensics |
| Healthy in CI | 6 of 10 | the other 4 have no healthcheck by design |
| End-to-end latency | 12 s upload→completed | Day-6 smoke run, CI log |
| k8s resources | 6 | `k8s/deployment.yaml`, kubeconform -strict |
| Published image | public, anonymously pullable | verified via registry manifest fetch, digest `sha256:1c4807a0…` |

## Interview prep — questions to be ready for

**Architecture**
- Q: Why Kafka instead of calling the workers directly?
  A: OCR and forensics are slow and independently scalable; the queue decouples
  upload latency from processing and gives a durable record. The honest
  counterpoint — a task queue like Celery (as in the sibling DevTrack) would
  have been simpler — is worth raising yourself: Kafka earns its place here
  because the event stream is also the audit trail, and the DLQ preserves
  failed events for replay.
- Q: What happens when a worker fails mid-message?
  A: Bounded retries, then a dead-letter envelope carrying the message, error
  type, attempt count and timestamp; the offset commits only after the DLQ
  write succeeds, because here the envelope is the only surviving copy.
  Contrast with DevTrack, where the Postgres row is authoritative and a failed
  DLQ publish must *not* block the commit. Same phrase, opposite correct
  answer — know which store is authoritative in each.
- Q: Why does `/analyze` return 202 and not the result?
  A: The result does not exist yet; the row is `analyzing` and clients poll
  `/status/{id}`. Fail-fast 503 while Kafka elects a broker is deliberate and
  tested, so an infra outage is distinguishable from a bad request.

**Detection**
- Q: Is this ML?
  A: No, and the README says so. OCR text plus pixel-level forensic heuristics
  (compression ghosts, copy-move style artifacts, layout inconsistencies)
  producing a weighted score. Claiming a trained classifier would be the single
  easiest lie to get caught in — the scoring function is readable in
  `app/forensics.py`.

**Ops**
- Q: What did booting the stack in CI actually catch?
  A: ZooKeeper exiting 1 with a `NullPointerException` in
  `CgroupV2Subsystem` — an old-JDK bug on cgroup-v2-only hosts — after which
  Kafka could not resolve it and every app container logged rdkafka connection
  refused. Fixed by bumping Confluent 7.3.0 → 7.9.10 (the last line shipping
  ZooKeeper, so no forced KRaft migration) plus a documented JVM flag; and a
  three-way `CREATE TABLE` race fixed with a Postgres advisory lock in
  `init_db()`.
- Q: Why an advisory lock rather than `CREATE TABLE IF NOT EXISTS`?
  A: SQLAlchemy's `create_all` doesn't emit IF NOT EXISTS; it reflects then
  creates, so two processes can both see "missing". The lock serializes them.
  Be honest that the run proves the stack boots *with* the lock, not that a
  collision was observed and prevented.

## Trade-offs / what you'd improve

- **Kubernetes has never been applied to a cluster.** Manifests are
  kubeconform-strict valid and cross-checked against compose, but pod startup,
  probes and HPA behaviour are unverified. Say so before it is discovered.
- **Coverage is measured now (78%, Day 7) but read its shape, not its total.**
  The Day-7 tests took `forensics.py` 13%→88% and `report.py` 12%→96%; before
  them the modules that ARE the product were the least tested in the repo,
  which is the single most useful thing measuring coverage revealed.
  `ocr.py` stays at 33%: it shells out to tesseract, and its behaviour is
  verified end to end by the compose smoke test, unmeasured. Say it that way.
- **Grafana starts in the stack but has no dashboards.** The Prometheus metrics
  are real and scraped; visualizing them is unbuilt work, not hidden work.
- **The scoring is heuristic**, with the weights and their rationale in code.
  A trained model would need labelled real-world forgeries, which do not exist
  here — that absence is the reason, not an oversight.
- The frontend is a dev-time client: built and linted in CI since Day 7, but
  not served by the compose stack and not tested beyond compilation.
