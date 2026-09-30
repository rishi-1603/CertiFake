# CertiFake — Certificate Forensics API

CertiFake analyzes uploaded certificate images/PDFs for signs of tampering
or fabrication, using OCR text extraction and pixel-level forensic
heuristics (not a trained ML classifier — see "How scoring works" below for
exactly what is and isn't implemented).

## Status: 7-day hardening pass complete, plus the Day-7 security remediation

This repository previously contained **three separate, overlapping
implementations** of the same idea (a Streamlit app, a synchronous FastAPI
demo backend, and an async Kafka/S3-backed FastAPI gateway), which had
drifted out of sync with each other, and one of them had authentication
**completely removed** in a prior commit. This section documents what is
true today, not what any of the old READMEs (or code comments) claimed.

**As of Day 1, the repo has been consolidated down to one backend:**
`backend/app/api.py` (a FastAPI + Postgres + Kafka + S3/MinIO service),
which is also what `docker-compose.yml` and `k8s/deployment.yaml` were
already pointed at before this cleanup — they just didn't match the code
running behind them. The Streamlit app and the standalone synchronous demo
backend have been deleted; their forensic logic was already just calling
into `backend/app/forensics.py` and `backend/app/ocr.py`, which are shared
and unaffected.

### What is implemented and verified today

- **Authentication (restored):** `/auth/register`, `/auth/login`,
  `/auth/me` — real Postgres-backed users, bcrypt password hashing, JWT
  bearer tokens. `/analyze`, `/status/{id}`, `/report/{id}`, and
  `/heatmap/{id}` all require a valid token.
  - This closes a real regression: a previous commit
    ("Remove authentication entirely for public access") had stripped auth
    from the old demo backend's `/analyze` and `/report/{id}` endpoints
    entirely, leaving them open to the public.
  - The consolidated version also adds a guarantee the *original* auth
    never had: analyses are scoped per-owner. Two different logged-in
    users cannot see each other's results, even by guessing/enumerating a
    valid analysis ID (verified by `backend/tests/test_ownership.py`).
- **Upload validation:** declared Content-Type allow-list, file extension
  cross-check, and real magic-byte sniffing via `libmagic` (so a file that
  is *renamed* to look like a PNG but isn't one gets rejected, not just a
  file with a "wrong" declared type) — see `backend/app/security.py`.
  Filenames are sanitized to their basename before being used to build a
  storage key, so a path-traversal filename like `../../etc/passwd.png`
  cannot escape the intended object-storage prefix.
- **OCR:** Tesseract + `pdf2image`, extracts raw text and regex-parses a
  handful of fields (certificate number, name, date, institution) —
  `backend/app/ocr.py`.
- **Forensic scoring (real, but modest — see below):** `backend/app/forensics.py`.
- **Distributed pipeline:** `/analyze` uploads to MinIO/S3, writes a
  Postgres row, and publishes a Kafka event; `worker-ocr` and
  `worker-forensics` consume events and update the row asynchronously.
  Clients poll `/status/{id}`.
- **Graceful degradation on infra outages:** if Kafka or MinIO/S3 is
  unreachable, `/analyze` returns a `503` with a clear message instead of
  hanging indefinitely or leaking a raw stack trace. Verified with a real
  (unreachable-endpoint) test in `backend/tests/test_failure_modes.py`, not
  just asserted in a comment.
- **Rate limiting on `/analyze` (Day 2):** Redis-backed, fixed-window
  (10 requests/60s per authenticated user, `app/rate_limit.py`) — the file
  the previous line's Kafka/S3 pipeline fans out to two worker processes
  is expensive enough that one scripted client hammering it could flood
  the whole pipeline, not just slow down the API. Returns `429` with a
  `Retry-After` header when exceeded. Deliberately fails *open* (allows
  the request) if Redis itself is unreachable — documented as a
  considered availability-over-throttling trade-off in the module
  docstring, not an oversight.
  - Audit finding fixed in passing: the `redis` package and a running
    `redis` container already existed in `requirements.txt` and
    `docker-compose.yml` before Day 2, with no application code ever
    calling either of them. This feature is the first real use of both.
- **Prometheus metrics (Day 3):** `GET /metrics` (unauthenticated, like
  `/health` — Prometheus itself has no way to send a JWT) exposes real
  domain metrics via `prometheus-client`, not just process/GC defaults:
  `certifake_analyze_requests_total{outcome=...}` (`accepted` /
  `rate_limited` / `storage_unavailable` / `broker_unavailable`),
  `certifake_analyze_request_duration_seconds` (the synchronous part of
  `/analyze` only — upload validation, S3 write, DB insert, Kafka
  publish — not the async OCR/forensics that happen afterwards), and
  `certifake_worker_events_processed_total` /
  `certifake_worker_events_failed_total` (labeled `worker=ocr|forensics`).
  See `backend/app/metrics.py`.
  - Audit finding fixed: `monitoring/prometheus.yml` **already existed in
    this repo before Day 3** and already targeted `api-gateway:8000`,
    `worker-ocr:8000`, and `worker-forensics:8000` on the default
    `/metrics` scrape path — but none of those three processes ever
    exposed a `/metrics` endpoint or any HTTP listener at all (the two
    workers are pure Kafka-consumer loops with no HTTP server). The
    `prometheus` and `grafana` services in `docker-compose.yml` existed
    too. All of it scraped nothing real until this change: both workers
    now also run `prometheus_client.start_http_server(8000)` alongside
    their consumer loop (see `backend/app/workers/ocr.py` and
    `backend/app/workers/forensics.py`), so all three scrape targets in
    the pre-existing config are real for the first time.
- **CI (GitHub Actions, Day 3):** `.github/workflows/ci.yml` runs on every
  push/PR to `main` — a `test` job (installs the same apt packages as
  `backend/Dockerfile`, lints with a pinned `ruff`, runs `pip-audit` as a
  **blocking** gate (it used to run `pip-audit --desc || true` with
  `continue-on-error`, i.e. non-blocking twice over — see "Security posture"
  below),
  runs the full pytest suite against isolated SQLite + fakeredis + faked
  Kafka/S3, no external services required) and a `docker-build` job that
  verifies `backend/Dockerfile` actually builds. It deliberately does
  **not** attempt `docker compose up`: this project's compose stack
  (Postgres + Redis + Zookeeper + Kafka + MinIO + 3 app services) was
  judged too heavy to reliably boot inside a shared CI runner within this
  7-day pass's scope — see "What is NOT implemented yet" below.
- **Reliable Kafka consumption (Day 4):** bounded retry with exponential
  backoff + full jitter, a real dead-letter topic, manual offset commits,
  and per-stage idempotency — all in one shared loop
  (`backend/app/consumer.py`) used by both workers, replacing two
  hand-rolled copies of the same poll loop.
  - **Manual commit is a correctness fix, not a tuning preference.**
    `confluent_kafka` defaults `enable.auto.commit` to **true** on a
    5-second timer, committing offsets regardless of whether the message was
    actually processed. A worker that crashed or raised mid-message could
    already have had that offset committed, so Kafka would never redeliver
    it: the upload would sit at `status="analyzing"` forever and the event
    was silently, permanently lost. That is at-most-once delivery.
  - This means an earlier claim in this README was **wrong**, and is
    corrected here rather than quietly deleted: it said a crashed worker
    "relies on Kafka's own consumer-group rebalance/redelivery". With
    auto-commit enabled, that safety net did not exist.
  - Manual commit gives **at-least-once** delivery, which makes duplicate
    delivery normal (a crash after processing but before committing
    redelivers the same event). So each worker also checks whether its own
    stage already ran — OCR skips when `ocr_text` is populated, forensics
    skips when `status == "completed"` — and reports the event as a
    duplicate instead of redoing expensive work and publishing a second
    downstream event. Retrying and deduplicating are not independently
    optional; the first is unsafe without the second.
  - Exhausted messages go to `<topic>.dlq` with the **original event
    preserved intact** plus error, error type, attempt count and timestamp,
    so a dead-lettered certificate can be inspected and replayed by hand
    rather than reduced to a log line nobody will find.
  - Commit ordering is deliberate: the offset is committed after success,
    after a duplicate, or after a *confirmed* DLQ publish — but **not** when
    the DLQ publish itself fails. Committing then would lose the message
    from both the source topic and the DLQ, which is the precise failure
    this exists to prevent. Both orderings are tested.
  - A worker now marks its analysis `failed` only *once retries are
    exhausted*, not on the first exception, so a transient MinIO or database
    blip is no longer permanently fatal to a certificate.
  - **Operational dependency, stated explicitly:** the `.dlq` topics are not
    pre-created anywhere — this relies on the broker's
    `auto.create.topics.enable` (Kafka's default, and what
    `docker-compose.yml` leaves unset). If a production broker disables
    auto-creation, the DLQ publish fails, and because the offset is then
    deliberately *not* committed, that message is redelivered indefinitely
    rather than lost. That is the intended bias (endless retry beats silent
    data loss), but it means DLQ topics should be provisioned explicitly in
    any deployment with auto-create off.
  - New Prometheus counters make this observable rather than inferable:
    `certifake_worker_event_attempts_total{attempt=...}`,
    `certifake_worker_events_dead_lettered_total`, and
    `certifake_worker_duplicate_events_skipped_total` (non-zero here is
    *expected* — it is the idempotency guard doing its job).
- **Deployment config made real and self-checking (Day 5):** the compose
  stack and k8s manifests had never been validated by anything — no Docker
  daemon existed where this was developed — and that hid a stack-breaking
  defect for 17 days.
  - **`minio/minio` no longer exists, and the whole stack was down because of
    it.** MinIO went source-only in Oct 2025, archived the community repo in
    Feb 2026, and on **2026-09-11 deleted `minio/minio` and `minio/mc` from
    Docker Hub** (quay.io and `bitnami/minio` are gone too). Because
    `api-gateway` waited on `minio: condition: service_healthy`, a fresh
    `docker compose up` failed at the *image-pull* step before a single
    container started. Nothing caught it: `docker-compose config` validates
    syntax, not whether an image still exists.
    Replaced with `cgr.dev/chainguard/minio`, **pinned by digest**
    (`sha256:6a1d0b45…`) rather than tag — an unpinned tag is the same failure
    class as the Day-3 `python:3.11-slim` incident. Chosen because it is
    vendor-maintained rather than a single-maintainer fork, is still the same
    software (so `MINIO_*` env vars and the app's S3 client contract are
    unchanged), and addresses the security angle directly: the final public
    MinIO image shipped with a known high-severity CVE and no upgrade path.
  - **The healthcheck had to change too, and this is the part that is easy to
    get wrong.** The old check was `curl -f …/minio/health/live`. That image
    contains `/usr/bin/mc`, `sh` and `bash` but **no curl, wget or nc** —
    established by downloading and listing all 11 layer tarballs, not guessed.
    Swapping the image alone would have left a healthcheck that fails forever,
    marking minio permanently unhealthy and wedging `api-gateway` behind it: a
    silent regression traded for a loud one. The check is now
    `mc alias set local …`, which authenticates against the server, so success
    proves the S3 API accepts requests rather than merely that a port is open.
    Its `$$VAR` escaping is deliberate — verified that a single `$` is
    interpolated from the *host* at parse time and collapses the command to
    `mc alias set local http://127.0.0.1:9000  ` with empty credentials.
  - `/data` in the replacement image is `drwxrwxrwx` and it runs as UID 65532
    (`nonroot`), both read from the image config blob. Docker seeds a fresh
    named volume with the image's permissions, so non-root works and
    `user: root` is **not** added. Caveat, stated rather than hidden: a
    `miniodata` volume created by the old root-running image is root-owned and
    would need deleting or `user: root` to migrate.
  - **Missing dependency edges.** Both workers set `MINIO_*` and download (and
    for forensics, upload) objects, yet neither declared `depends_on: minio`,
    so they could start before object storage was accepting requests. Unlike
    Kafka, an S3 fetch is one-shot with no reconnect loop, so this one
    genuinely needs ordering. `api-gateway` set `REDIS_URL` with no redis edge
    either — added, though it is ordering-only, since `app/rate_limit.py` fails
    **open** on `redis.RedisError`.
  - **Dead config removed rather than papered over.** Both workers also set
    `REDIS_URL`, but only `app/api.py` imports `app/rate_limit.py` — the
    workers never touch Redis. The variable was removed instead of adding a
    dependency edge to justify it.
  - **A Kafka readiness race was suspected and then disproved.** `kafka` and
    `zookeeper` have no healthchecks, so dependents use
    `condition: service_started`. That looks wrong, but the consumer loop is
    self-healing: `poll()` returns `None` while the broker is unavailable,
    broker/partition errors are logged rather than fatal
    (`backend/app/consumer.py`), and confluent-kafka reconnects on its own
    background thread. Adding a Kafka healthcheck would mean trusting a probe
    binary nobody has verified exists inside `cp-kafka`; if absent, the broker
    would be marked permanently unhealthy and every dependent would never
    start — converting a stack that recovers by itself into one dead on
    arrival. Left as `service_started`, deliberately.
  - Pinned `prom/prometheus:v3.15.0` and `grafana/grafana:13.0.9` (both were
    `:latest`), removed the obsolete top-level `version:` key, and added
    healthchecks plus `restart: unless-stopped`. App-service healthchecks use
    **Python, not curl**: `backend/Dockerfile` installs tesseract, libgl1,
    libglib2.0-0t64, poppler-utils and libmagic1t64 on
    `python:3.11-slim-trixie`, none of which provide curl.
  - **k8s manifests rewritten.** They previously passed `kubeconform -strict`
    while shipping `image: your-dockerhub-user/certifake-api:latest` (a
    repository that does not exist), referencing a Secret defined nowhere, and
    deploying **only one of the two workers** — a validator cannot see a
    placeholder or an omission. Now: one real image
    (`ghcr.io/rishi-1603/certifake-backend`, published by CI) serving all three
    workloads with different commands, exactly as compose does; the missing
    `worker-forensics` Deployment added; liveness/readiness probes on `/health`
    (API) and `/metrics` (workers, which really do serve that port — the
    metrics server starts *before* the never-returning consume loop in each
    worker's `__main__` block); `securityContext` dropping all capabilities;
    `Service` type changed `LoadBalancer` → `ClusterIP` because provisioning a
    public cloud LB is a provider-specific cost/security decision a template
    should not assume.
  - **Two sources of truth for replica count, fixed.** The OCR Deployment set
    `replicas: 3` while its HPA declared `minReplicas: 2`, so every
    `kubectl apply` would reset the scaled count. `replicas` is now omitted
    from HPA-managed Deployments. CPU-based autoscaling is kept and is
    defensible *here specifically*: OCR shells out to `tesseract`, so the
    workload is CPU-bound and utilisation tracks load. For an I/O-bound
    consumer the right signal is lag (KEDA), which is deliberately **not**
    added — no cluster exists to validate it against, so it would be
    technology for its own sake.
  - **Three new CI checks, none of which needs a Docker daemon:**
    `docker compose config` (syntax/interpolation), `kubeconform -strict`
    (real Kubernetes schemas), and `scripts/check_images.py`, which resolves
    every image reference — including Dockerfile `FROM` lines — against its
    registry via the anonymous v2 API and fails if any can no longer be
    pulled. **That last check is the one that would have caught the MinIO
    deletion on the day it happened.** Docker Hub reports a nonexistent
    repository with the same 401 it uses for a private one, so the script
    distinguishes them by whether the token service granted any pull scope —
    a heuristic validated against known-good (postgres, redis, cp-kafka) and
    known-bad (`minio/minio`, `bitnami/minio`) controls.
  - `scripts/check_config_consistency.py` asserts the files *agree*, which is
    the drift no single-file validator can see: k8s deploys exactly the app
    workloads compose builds; every HPA targets a Deployment that exists and
    does not fight it over `replicas`; a service that sets `MINIO_*`/`REDIS_URL`/
    `DATABASE_URL`/`KAFKA_*` has the matching `depends_on` edge; every
    Prometheus target is a real compose service that mentions that port; no
    placeholder image strings; and every Secret key the manifests reference is
    documented below. It found the two missing worker dependency edges and the
    undocumented Secret on its first run.

### Kubernetes (`k8s/deployment.yaml`) — what is and is not true

**Never applied to a live cluster.** Verified: schema validity
(`kubeconform -strict`, in CI and locally) and cross-file consistency with the
compose stack. Not verified: that pods start, that probes pass, that the HPAs
scale, or that Kafka/Postgres/Redis/MinIO are reachable in-cluster.

Postgres, Redis, Kafka and MinIO are **assumed external** — these manifests do
not deploy them, and the hostnames used (`kafka-service`, `redis-service`,
`minio-service`) must exist in the namespace. Standing up a broker and a
database in-cluster is a different project with different operational stakes.

The Secret is deliberately **not** committed (a repo should never carry
credentials, even fake-looking ones). Create it before applying:

```bash
kubectl create secret generic certifake-secrets \
  --from-literal=database-url='postgresql://USER:PASS@HOST:5432/certifake_db' \
  --from-literal=secret-key='<a real random value>' \
  --from-literal=minio-access-key='<access key>' \
  --from-literal=minio-secret-key='<secret key>'
```

Those four keys (`database-url`, `secret-key`, `minio-access-key`,
`minio-secret-key`) are exactly the ones the manifests reference —
`check_config_consistency.py` fails CI if they drift apart.

```bash
kubectl apply -f k8s/deployment.yaml
```

### Automated tests: 89 pytest tests covering auth, cross-user ownership
  isolation, upload validation, the storage-outage failure path,
  rate-limiting (including a real Redis-outage simulation), the `/metrics`
  endpoint, the consumer loop's retry / DLQ / commit-ordering /
  idempotency guarantees, concurrent-safe schema creation, direct unit tests
  of the forensic scoring core and of PDF report generation, and — added with
  the Day-7 security remediation — the configuration and JWT-hardening tests
  (`backend/tests/`).

  Coverage is measured in CI (`--cov=app`, production code only via
  `.coveragerc`): **78%** as of Day 7. The honest shape of that number:
  `app/forensics.py` went from 13% to 88% and `app/report.py` from 12% to 96%
  once the Day-7 tests landed, while `app/ocr.py` remains at 33% because it
  shells out to tesseract — its behaviour is verified end to end by the
  compose smoke test in CI, which does not measure coverage. Writing those
  tests also surfaced a real limitation: `create_report()` slices the OCR
  preview to 32 wrapped lines, so the `showPage()` branch below it is
  unreachable and longer OCR text is silently truncated. Recorded under
  Future Improvements; the test pins the truncation as it exists.

  The thirteen newest are `test_config_and_auth_hardening.py`. They pin the three
  hardening changes below, including two that can only be tested in a
  subprocess: because both values are read at import time and `conftest.py`
  sets them for the rest of the suite, "the process refuses to start without
  them" is not observable from inside a running test. One of them is a
  regression test for the specific PyJWT advisory the bump addresses — a
  forged token whose payload is JSON nested 20,000 deep must produce a 401,
  not an unhandled `RecursionError` on the auth path.

  The last six are `test_schema_init.py`, added Day 6. They cover
  `app/models.py:init_db()`, which now serializes `create_all()` behind a
  Postgres advisory lock because api-gateway, worker-ocr and
  worker-forensics all import that module and would otherwise race on
  `CREATE TABLE` at boot. The SQLite branch is exercised for real; the
  Postgres branch's *contract* is pinned with a fake engine (lock → create →
  unlock, same key both ways, unlock still runs when `create_all` raises),
  because the suite deliberately runs on SQLite and advisory locks have no
  SQLite equivalent. The file's docstring states what no test covers: that a
  real Postgres actually blocks the second process.

### Security posture (Day-7 remediation)

A dependency audit and a read of the auth, config and upload paths produced
findings that are recorded with severity, evidence and practical exposure in
the portfolio audit's security register. Everything rated HIGH or above for
this repo is fixed here; the fixes are listed with what made each one matter.

1. **The dependency scan now gates the build.** It previously ran
   `pip-audit --desc || true` *and* carried `continue-on-error: true`. On the
   last green build before the change it printed "Found 77 known
   vulnerabilities in 10 packages" and reported `success`. The pin set is now
   clean — `No known vulnerabilities found`, verified both against the
   installed environment and against `pip-audit -r requirements.txt` so the
   resolution CI performs is the one that was checked — and only then were the
   two escape hatches removed. Zero waivers. The workflow comment states the
   rule for the future: an unfixable advisory gets an explicit
   `--ignore-vuln <ID>` with a written reason and a re-review date.
2. **python-jose is gone; the JWT library is PyJWT 2.15.1.** python-jose 3.3.0
   carried PYSEC-2024-232/233 (fixed in 3.4.0) and PYSEC-2025-185, which has
   **no published fix**, and the project is effectively unmaintained. It also
   pulled in `ecdsa`, which has an unfixed advisory of its own. The API surface
   used was three calls (`encode`, `decode`, one `except`), so the migration is
   exact rather than approximate, and a test asserts both that `jose` is no
   longer imported and that token expiry is still enforced through the real
   endpoint.
3. **Pillow 11.0.0 → 12.3.0 (19 advisories) and python-multipart 0.0.20 →
   0.0.32 (12).** Both were directly reachable rather than theoretical:
   `Image.open()` runs on attacker-supplied files (`forensics.py:37,43`,
   `ocr.py:37`) and untrusted uploads are parsed at `api.py:46,177,282`. The
   forensics tests assert pixel-level scoring behaviour, so they are the
   evidence the Pillow bump changed nothing about the product; all 89 pass.
4. **`SECRET_KEY` is now required, and must be at least 32 bytes.** It used to
   default to `change_me_to_a_long_random_secret` — a string committed to a
   public repository, so a deployment that forgot the variable would still start
   and still issue tokens that *anyone* could forge. The sibling DevTrack and
   Repay-Master projects made this field required on Day 3; this repo was the one
   still carrying the fallback. Missing values now fail at import.

   Requiring the field stops a *missing* secret; the length floor (finding S15)
   stops a *weak* one, which is the failure that survives a checklist because
   everything appears to work. HS256 uses the secret directly as an HMAC key and
   RFC 7518 3.2 requires at least the hash output length — below that, a
   signature can be brute-forced offline from a single captured token. Unlike the
   two siblings, this check is **unconditional** rather than production-only, and
   the asymmetry is deliberate: both siblings are deployed or intended to be
   (DevTrack is live on Render), where a rule that fails unconditionally could
   take a running service down over a development key. CertiFake is not deployed
   anywhere — no cluster has ever run these manifests — so the stricter rule
   costs nothing today and is the right one the day it is. The CI compose-smoke
   secret was 31 characters and is now 37.
5. **`DATABASE_URL` is now required.** It used to fall back to
   `sqlite:///./certifake.db`, so a container started without it ran silently
   on a file-backed SQLite — no error, no persistence across restarts, and a
   quiet divergence from the Postgres the rest of the stack was using. The
   quick-start instructions below were updated to match, because the old ones
   relied on that fallback.
6. **Five dead dependencies removed**, each verified to have zero import sites
   anywhere in `app/`, `tests/` or `scripts/` before deletion: `jinja2`
   (carried an advisory), `PyPDF2` (carried an advisory, and its project was
   renamed to `pypdf`, so the advisory's stated fix version is not even
   installable under that name), `pdfplumber` (unused, and the reason
   `pdfminer-six`'s four advisories were in this app at all), `aiofiles`, and
   `alembic` — that last one had no `alembic.ini` and no `migrations/`
   directory; schema creation goes through `models.init_db()` behind a Postgres
   advisory lock. Keeping it was worse than dead weight, because it implied a
   migration story that does not exist. (Alembic is real in the sibling
   DevTrack, where a CI job runs the migrations against a live Postgres.)
7. **Floating version specifiers pinned.** `numpy`, `opencv-python-headless`
   and `psycopg2-binary` used `>=`, so two builds of the same commit could
   install different code. All pins are now exact and match versions this suite
   was run against. `ruff` is pinned in CI for the same reason: an unpinned
   linter lets an upstream rule change break a build with no commit here.

   The first attempt at this pinned correctly for the wrong machine, and the
   red build is worth recording rather than quietly fixing: `numpy` was set to
   2.5.3, which publishes wheels only for Python ≥3.12, chosen from a local
   3.13 venv where it installed without complaint. This repo builds and tests on
   **3.11** (`ci.yml` and `backend/Dockerfile`), so the pin resolved nowhere in
   CI and the `test` job failed at install time — `ERROR: No matching
   distribution found` — on a commit whose every other change was sound. It is
   now `numpy==2.4.6`: the newest release with a cp311 wheel, advisory-clean
   under `pip-audit`, and what this suite is actually run against. The
   constraint and the exact cross-interpreter check that would have caught it
   (`pip install --dry-run --python-version 3.11 --platform manylinux2014_x86_64
   --only-binary=:all: -r requirements.txt`, which reproduces the CI failure
   locally in four seconds) are recorded in `requirements.txt` itself, because
   that is where the next person edits a pin.
8. **A parity guard so the interpreter cannot drift silently**
   (`scripts/check_config_consistency.py`, `check_python_parity`). The numpy
   mistake above was not really about numpy: it was about the repo having no way
   to say "the version you are pinning against is not the version that runs
   here." The checker now compares `python-version` in `ci.yml` against every
   `FROM python:X.Y` in the repo's Dockerfiles and fails the
   `config-validation` job if they disagree — the quieter mirror-image case,
   where the suite passes on 3.12 and the image runs 3.11, is the one that would
   otherwise reach production. Mutation-tested locally: it exits 1 with the
   mismatch spelled out when the Dockerfile says 3.13 and CI says 3.11, and 0
   when they agree. This script is deliberately identical across all three
   repositories, so the same guard runs in DevTrack and Repay-Master (both on
   3.12).


CORS is *not* a finding in this repo, and it is worth saying why rather than
leaving it implicit: `allowed_origins` defaults to an explicit list of
localhost origins, never `*`, so the wildcard-with-credentials pairing that the
two sibling repos had cannot arise here.

Not done, and not claimed: `passlib` 1.7.4 is unmaintained (no advisory
against it today, so nothing forced the change) and would be better replaced by
calling `bcrypt` directly; `psycopg2-binary` is convenient but the Postgres
docs recommend a source build for production images. Both are in Future
Improvements.

### Future Improvements (deliberately not built yet, with the reason each is deferred)

- Grafana dashboards for the new Prometheus metrics have not been built —
  the metrics are real and scrapeable, and Grafana does start as part of the
  compose stack, but nothing has been done to visualize them.
- **`create_report()` silently truncates the OCR preview to 32 wrapped lines.**
  Found on Day 7 by a test that expected the opposite: the drawing loop
  contains a `y < 60 → showPage()` pagination branch, but because the preview
  is sliced `[:32]` first, 32 lines can never drive `y` below 60 — the branch
  is unreachable through the only input that feeds it, and longer OCR text is
  dropped from the PDF without any indication. Deliberately NOT fixed here:
  whether a client report should paginate or truncate is a product decision,
  and "fixing" it silently would change an artifact nobody has reviewed. The
  test pins current behaviour; this bullet records the decision still owed.
- **`app/ocr.py` has no direct unit tests (33% coverage).** It shells out to
  tesseract, so unit-testing it means either installing tesseract in the test
  environment or faking the binary; the compose smoke test already verifies it
  end to end against the real thing. Adding fast, faked unit tests for the
  field-extraction regexes specifically is worthwhile and unbuilt.
- **`passlib` 1.7.4 is unmaintained.** No advisory is filed against it, so
  nothing forced a change during the Day-7 remediation, and swapping it while
  touching the auth path would have mixed two kinds of risk in one commit.
  Calling `bcrypt` directly (or moving to argon2) removes a dependency that
  will not receive fixes; the visible symptom today is a trapped
  "(trapped) error reading bcrypt version" warning, because passlib 1.7.4
  predates bcrypt 4.x's API.
- **`psycopg2-binary` in the production image.** Fine for CI and for this
  compose stack; the psycopg2 docs recommend building from source (or using
  psycopg 3) for production, because the binary wheels bundle a libpq that may
  not match the target platform.
- **Resolved (Day 6, verified by a passing CI run):** CI *does* now run the
  full `docker-compose` stack end-to-end, and there *is* a real integration
  test against actual Kafka/Postgres/MinIO containers. Both of the bullets
  that used to sit here said those things were missing; they were accurate
  until the `compose-smoke-test` job passed on commit `62b6c7f` (run
  `36613263530`, 2026-09-29). See **"Docker Compose stack — booted and driven end to end"** below for exactly what that run
  observed — and for the two real bugs it found, in ZooKeeper and in this
  project's own startup path.
- Malware scanning of uploaded files (only type/format validation exists).
- `k8s/deployment.yaml` has **never been applied to a live cluster**. It is
  schema-validated and internally consistent (see Day 5 below), but pod
  startup, probe behaviour, HPA scaling and reachability of
  Kafka/Postgres/Redis/MinIO from inside a cluster are all unverified, and
  no cluster was available to verify them.
- **Resolved (Day 5, verified after the fact):** CI *does* now publish a
  container image. The `docker-publish` job ran on commit `5a31222` and
  succeeded; `ghcr.io/rishi-1603/certifake-backend` was then confirmed
  **publicly resolvable anonymously** — both `:latest` and the immutable
  `:<git-sha>` tag return HTTP 200 on an unauthenticated registry manifest
  fetch (digest `sha256:1c4807a0…`). So the k8s manifests reference a real,
  pullable artifact rather than a placeholder, and `imagePullSecrets` are
  **not** required. This README previously said the answer would be recorded
  once CI had actually run it; this is that record.
  - Still unverified: that a pod actually *starts* from that image in a
    cluster. Publishing proves the image exists and is pullable, not that the
    app runs inside it.

### Docker Compose stack — booted and driven end to end

**Verified in CI, not locally.** The `compose-smoke-test` job runs
`docker compose up -d --build --wait --wait-timeout 900` on the real
10-service stack and then executes `scripts/compose_smoke_test.py` *inside*
the api-gateway container, so it uses the application image's own Pillow and
needs no dependencies on the runner. Passing on commit `62b6c7f` (run
`36613263530`, 2026-09-29).

What that run observed:

- **All 10 containers up, none restarting or exited.** Six reported Docker's
  own `healthy` state — `postgres`, `redis`, `minio`, `api-gateway`,
  `worker-ocr`, `worker-forensics` — which is the first *empirical*
  confirmation that the healthchecks work. They had previously been verified
  only by unpacking image layer tarballs to confirm the binaries they name
  (`pg_isready`, `redis-cli`, `mc`, `python`) actually exist in those exact
  images. The other four (`zookeeper`, `kafka`, `prometheus`, `grafana`) have
  no healthchecks by design, so `--wait` does not gate on them.
- **The Chainguard MinIO replacement works.** `minio` reached `healthy`, so
  the `mc alias set` probe authenticates against the S3 API successfully as
  UID 65532 — the open question left by Day 5's image swap.
- **One real request traversed the whole pipeline.** `/health` 200 →
  `/auth/register` 201 with a JWT → `/auth/me` 200 → a 37,828-byte PNG
  rendered with real text → `POST /analyze` **202 on the first attempt** →
  `status=completed` in **12 seconds** → 170 characters of OCR text with
  `CERTIFICATE` recovered from the image → `authenticity_score = 50.0` from
  the forensics worker → **all four** extracted fields (`name`,
  `certificate_no`, `date`, `institution`) → `GET /heatmap/{id}` 200 with a
  51,034-byte PNG and `GET /report/{id}` 200 with 2,091 bytes, both stored in
  MinIO by a worker and served back through the API.

That exercises every hop: HTTP → magic-byte validation → MinIO write →
Postgres row → Kafka publish → OCR worker → tesseract → Kafka publish →
forensics worker → Pillow scoring → heatmap back to MinIO → row updated →
client polls and downloads. The unit tests fake Kafka and S3 at the
Python-call boundary, so none of this was covered before.

**Two real bugs this found, both fixed:**

1. **ZooKeeper could not start on a cgroup-v2 host.** The first run failed
   with `container certifake-zookeeper-1 exited (1)` and a
   `NullPointerException` in `jdk.internal.platform.cgroupv2.CgroupV2Subsystem`
   — a known JDK bug on cgroup-v2-only hosts whose container cgroup has no
   delegated controllers, triggered here by the JMX local management agent.
   Kafka then could not resolve `zookeeper:2181` and every app container
   logged rdkafka `Connection refused`: the entire event backbone was down
   because its metadata store died during JVM startup. `cp-zookeeper:7.3.0` is
   a January 2023 image with a JDK old enough to hit this. Fixed by bumping
   both Confluent images to **7.9.10** — the last Confluent line that still
   ships ZooKeeper, since 8.0 removes it and mandates KRaft, so this is the
   newest image preserving the existing architecture rather than forcing a
   KRaft migration into the same commit as a boot fix — plus
   `JAVA_TOOL_OPTIONS=-XX:-UseContainerSupport` on both JVMs and explicitly
   pinned heap sizes. `docker-compose.yml` documents the reasoning and warns
   not to copy that flag into `k8s/deployment.yaml`, which does set resource
   limits.
2. **Three containers raced on `CREATE TABLE` at boot.** api-gateway,
   worker-ocr and worker-forensics are one image with three commands and all
   three import `app/models.py`, which called `Base.metadata.create_all()` at
   import time. `create_all(checkfirst=True)` reflects and then emits a plain
   `CREATE TABLE`, not `IF NOT EXISTS`, so on a fresh Postgres two processes
   can both see "missing" and the loser dies with sqlstate `42P07` during
   import. `restart: unless-stopped` would usually hide that, turning startup
   into a scheduling race. `init_db()` now serializes it behind a Postgres
   session-level advisory lock, and `app/api.py`'s second bare `create_all()`
   (with the `Base`/`engine` imports that existed only to call it) is gone.
   This one was found by reading the code while writing the test, not by
   observing a crash — stated as such rather than dressed up as a caught
   failure.

**Still not verified:** nothing about Kubernetes (next section), and the
advisory-lock path has not been observed *blocking a second process* on a real
Postgres — the CI run proves the stack boots and works with the lock in place,
not that a concurrent collision was actually prevented.

### How scoring works (exactly, not aspirationally)

`score_document()` starts at a baseline of 50 and applies these adjustments:

| Signal | Condition | Score impact |
|---|---|---|
| Low-resolution upload | image smaller than 300x300 after downscaling | −10 |
| Compression inconsistency | mean Error-Level-Analysis diff > 15 | −18 |
| Unusual edge pattern | Canny edge density < 2% of pixels | −6 |
| Weak OCR text | extracted text under 30 characters | −15 |
| Missing expected certificate keywords | none of "certificate/degree/diploma/issued/university/completion" found in OCR text | −7 |
| PDF bonus | uploaded file is a PDF | +5 |

Final score is clamped to 0–100. Verdict thresholds (from
`backend/app/workers/forensics.py`): **≥80 = Likely Genuine, 55–79 = Needs
Review, <55 = Likely Fake.**

If OpenCV is unavailable at runtime, `forensics.py` falls back to a
text-only heuristic (`_fallback_score`) using only the OCR-keyword and
text-length checks — this fallback path exists in the code but is not the
normal path (`CV_AVAILABLE=True` in the shipped Docker image and in this
project's dev environment).

This is **heuristic image forensics**, not a trained classifier and not a
guarantee of authenticity — see the Disclaimer below. Some claims from an
earlier version of this README (QR code verification, EXIF metadata
forensics, noise-pattern analysis, texture analysis, layout analysis, "12+
forensic checks") described the retired Streamlit app, not this backend,
and have been removed rather than left inaccurate.

## Architecture

```
Client (React frontend, or curl/Postman)
  |
  |  POST /auth/register, /auth/login   -> JWT
  |  POST /analyze (Bearer token)       -> 202 Accepted, analysis_id
  v
FastAPI gateway (app/api.py)
  |         \
  |          -> S3/MinIO: raw file bytes stored at {user_id}/{analysis_id}/{filename}
  |          -> Postgres: CertificateAnalysis row (status=analyzing)
  |          -> Kafka topic "certificate_uploaded"
  v
worker-ocr (app/workers/ocr.py)
  |  downloads file, runs Tesseract OCR + field extraction
  |  writes ocr_text/extracted_fields to Postgres
  |  publishes Kafka topic "ocr_completed"
  v
worker-forensics (app/workers/forensics.py)
  |  downloads file, runs score_document() (OpenCV ELA + edge + OCR heuristics)
  |  uploads heatmap PNG to S3/MinIO
  |  writes score/verdict/signals/status=completed to Postgres

Client polls GET /status/{analysis_id} (Bearer token, ownership-checked)
Client downloads GET /report/{analysis_id} (PDF) and GET /heatmap/{analysis_id} (PNG)
```

Every service in `docker-compose.yml` (`api-gateway`, `worker-ocr`,
`worker-forensics`) builds from the same `backend/Dockerfile` and runs a
different entrypoint command against the same codebase — there is exactly
one Python package (`backend/app/`) now, not three.

## Running locally

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# System dependencies (Ubuntu/Debian) for OCR + PDF handling:
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng poppler-utils libmagic1

cp .env.example .env   # then set a real SECRET_KEY (see the comment in that file)

# Quickest way to try the API without Kafka/Postgres/MinIO running: point
# DATABASE_URL at a local SQLite file yourself. It is NOT optional any more --
# the implicit sqlite fallback was removed in the Day-7 remediation, because a
# container started without DATABASE_URL used to run silently on a file-backed
# database and lose it on restart. Kafka/S3 calls still fail fast (503) without
# those services; for the full pipeline, use docker-compose instead:
DATABASE_URL=sqlite:///./certifake.db uvicorn app.api:app --reload
```

### Full stack (Postgres + Kafka + MinIO + workers)

```bash
# from the repo root
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
docker-compose up --build
```

This starts Postgres, Kafka (+ Zookeeper), MinIO, the API gateway, both
workers, and Prometheus/Grafana. As of Day 3, Prometheus has real targets
to scrape (`api-gateway:8000`, `worker-ocr:8000`, `worker-forensics:8000`,
all serving `/metrics`) — no Grafana dashboards have been built for them
yet (see "What is NOT implemented yet" above).

### Tests

```bash
cd backend
pip install -r requirements.txt
pytest -v
```

89/89 tests currently pass. They use a throwaway SQLite database and mock
Kafka/S3 calls at the Python function boundary for most tests; one test
suite (`test_failure_modes.py`) points the *real* Kafka/S3 client code at
an intentionally unreachable address to verify the 503 failure-handling
behavior actually works, not just that it's mocked to look like it works.
Separately from the unit suite, `scripts/compose_smoke_test.py` drives one
real request through the actual containers in CI — see the Docker Compose
section above for what that run observed.

## Frontend

`frontend/` is a small React (Vite) app with a login/signup form and a
drag-and-drop upload UI that polls `/status/{id}` and renders the result,
heatmap, and a PDF report download. Every one of those claims is checked
against `src/App.jsx` in `frontend/README.md`, which also states what the app
deliberately does *not* have.

```bash
cd frontend
npm ci          # exact install from the lockfile, not npm install
npm run dev
```

Set `VITE_API_URL` if the backend isn't at `http://127.0.0.1:8000`. The
`frontend-build` CI job compiles and lints this directory on every push; it
was added on Day 7 because until then nothing had ever built it.

## Disclaimer

This tool provides a **probabilistic, heuristic forensic assessment**
based on a small number of image-analysis signals (see the scoring table
above) — it is not a machine-learning classifier trained on labeled
genuine/fake certificates, and it is **not** a substitute for official
verification by the issuing institution or a professional forensic
document examiner.
