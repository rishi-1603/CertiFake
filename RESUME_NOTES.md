# CertiFake — Resume & LinkedIn Bullet Points

*Added Day 7 in the same format as the siblings' notes; every figure measured.
Test count and coverage come from the CI `test` job, which gained `--cov=app`
on Day 7 — before that no percentage existed and none was quoted, which was
the honest position at the time. Coverage is measured on production code only
(`.coveragerc` omits the test directory); see the table and the note below for
what the 78% does and does not mean. Re-measured again after the Day-7
security remediation: 89 cases, 78% of 768 production statements, and a
dependency audit that is clean **and** blocking.*

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
| pytest cases | 89 | CI `test` job; `def test_` count matches (76 before the Day-7 hardening tests) |
| Coverage (production code) | 78% | CI `--cov=app` with `.coveragerc` omitting tests; 768 stmts, 167 missed |
| Dependency audit | clean, and blocking | `pip-audit` on the pinned set: `No known vulnerabilities found`; was 77 across 10 packages, and the CI step had both `\|\| true` and `continue-on-error` |
| JWT library | PyJWT 2.15.1 | `app/auth.py`; python-jose removed — unmaintained, and PYSEC-2025-185 has no published fix |
| Required config | `SECRET_KEY`, `DATABASE_URL` | no insecure defaults left; subprocess tests assert the process refuses to start without them |
| Signing-key floor | 32 bytes, unconditional | `app/config.py` validator; stricter than the siblings on purpose, because nothing here is deployed |
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

**Security**
- Q: What did the security review actually find, and what did you do about it?
  A: Seven things worth naming. (1) The CI dependency scan was non-blocking
  twice over — `pip-audit --desc || true` plus `continue-on-error` — so a green
  build was reporting 77 known vulnerabilities across 10 packages. (2) The JWT
  library was python-jose 3.3.0: unmaintained, with one advisory that has no
  published fix at all, dragging in `ecdsa` which has another. (3) Pillow
  11.0.0 had 19 advisories and python-multipart 12, and both were *reachable*
  rather than theoretical — `Image.open()` runs on uploaded files and the upload
  endpoints parse untrusted multipart. (4) `SECRET_KEY` defaulted to a string
  committed to a public repo, so a deployment that forgot the variable would
  still issue tokens anyone could forge. (5) `DATABASE_URL` silently fell back
  to a file-backed SQLite, so a misconfigured container lost data on restart
  without erroring. (6) Five dependencies were installed that nothing imported,
  including `alembic` with no migrations directory — dead weight that implied a
  migration story the repo does not have. (7) Three version specifiers were
  floating, so two builds of one commit could install different code. (8)
  `SECRET_KEY` had no minimum length, so HS256 could have been running on a key
  short enough to brute-force offline from a single captured token -- the failure
  mode that survives a checklist, because everything appears to work. All eight
  are fixed; the scan is clean with zero waivers, and only then did I make it
  block.
- Q: Which of those advisories were you actually vulnerable to?
  A: That distinction is the part worth being precise about. Reachable: Pillow
  and python-multipart, because untrusted bytes go straight into them; and the
  PyJWT payload-recursion DoS after the bump, which is why there is a
  regression test asserting a forged 20,000-deep-nested token returns 401
  rather than an unhandled `RecursionError`. Not reachable, and I would say so
  rather than inflate the count: the Starlette advisories concern
  `StaticFiles`/`FileResponse`, `request.url.hostname`, bare `HTTPEndpoint` and
  urlencoded form limits — this app uses none of them. A CVE count is not a
  risk assessment.
- Q: Why did you remove `alembic` instead of writing migrations?
  A: Because the pin was a claim the repo could not support. There was no
  `alembic.ini` and no `migrations/`; schema creation runs through
  `models.init_db()` behind a Postgres advisory lock, which is a legitimate
  design for this app and is tested for concurrent safety. Writing migrations
  would have been the better engineering choice if the schema were evolving
  under load — but silently keeping a dependency that implies it, in a repo
  being read for evidence, was the worse option. The sibling DevTrack is where
  Alembic is real, and there a CI job runs the migrations against live
  Postgres.

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
- **`passlib` 1.7.4 is unmaintained and `psycopg2-binary` is not what the
  Postgres docs recommend for production images.** Neither had an advisory, so
  neither was forced by the Day-7 remediation, and changing the password-hashing
  library in the same commit as the JWT migration would have mixed two kinds of
  risk. Both are in the README's Future Improvements with the reasoning; the
  visible symptom of the passlib/bcrypt version gap is a trapped
  "(trapped) error reading bcrypt version" warning in the logs.
- **Grafana starts in the stack but has no dashboards.** The Prometheus metrics
  are real and scraped; visualizing them is unbuilt work, not hidden work.
- **The scoring is heuristic**, with the weights and their rationale in code.
  A trained model would need labelled real-world forgeries, which do not exist
  here — that absence is the reason, not an oversight.
- The frontend is a dev-time client: built and linted in CI since Day 7, but
  not served by the compose stack and not tested beyond compilation.
