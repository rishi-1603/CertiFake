# Database and cache evidence — CertiFake (Day 7, 2026-10-08)

Every number here came from a real PostgreSQL 17.11 (scratch database `day7_cf`, 1,000 users and
20,000 analyses seeded, `ANALYZE` run) on the machine that runs this project's tests. Full
transcripts: `day7/raw/cf-pg-plans.txt`, `day7/raw/cf-advisory-lock-race.txt`,
`day7/raw/cf-commit-order.txt`.

## 1. Query plans — the whole query surface

The Day-7 plan asked for plans on "document-list, verdict-lookup and report paths". Recon found
that two of those three **do not exist in this codebase**: there is no list endpoint and no
status filter anywhere, so no query filters or sorts on any column that would need an index.
Every read is a primary-key lookup:

| Query | Call sites | Plan |
|---|---|---|
| `SELECT * FROM analyses WHERE id = ?` | `api.py:165`, `workers/ocr.py:49,72`, `workers/forensics.py:37,55` | Index Scan using `ix_analyses_id` — 0.030 ms |
| `SELECT * FROM users WHERE email = ?` | `auth.py:78,91` (register, login) | Index Scan using `ix_users_email` — 0.013 ms |
| `SELECT * FROM users WHERE id = ?` | `auth.py:112` | Index Scan using `ix_users_id` — 0.025 ms |
| newest 10 analyses for one user *(hypothetical — no such endpoint)* | — | Bitmap Index Scan on `ix_analyses_user_id` + sort of 20 rows — 0.052 ms |

**No indexes were added, and that is the finding**: adding indexes "for every filtered column"
would have produced indexes that nothing filters on. The one shape a future list endpoint would
need is `(user_id, created_at DESC)`; it is not created until the endpoint is.

## 2. Advisory-lock schema creation — reproduced, both directions

`app/models.py` serialises schema creation with `pg_advisory_lock` and its own docstring says the
race was "reasoned about from the code … rather than reproduced. It is fixed here because it is a
startup-ordering defect that would otherwise only ever appear as an intermittent boot failure."

Day 7 reproduced both halves, 8 processes per round released from a spin-barrier on a fresh
database (`day7/scripts/cf_lock_race.py`):

| Algorithm | Round 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| bare `MetaData.create_all()` (no lock) | 6/8 failed | 7/8 | 2/8 | 3/8 | 5/8 | 3/8 |
| `init_db()`'s locked sequence | 0/8 | 0/8 | — | — | — | — |

Failures were `DuplicateTable: relation "users" already exists` (42P07, exactly what the
docstring predicts) and `UniqueViolation on pg_type_typname_nsp_index` (the same collision one
level lower). Every round still ended with both tables present — the losers die, the database is
fine, and with `restart: unless-stopped` the symptom would be an intermittent boot, which is why
it is worth a lock rather than a retry.

One wrinkle the first version of the probe hit, and it changes the scope of the fix: the first
attempt raced 8 processes that each did `from app.models import Base; create_all(...)` and
reproduced **nothing**, because `app/models.py` calls `init_db()` at **import**. The import is
the widest door into this race, and it goes through the lock too.

## 3. Commit-then-publish ordering — verified against a real database

Both workers claim in comments that they publish only after the DB commit
(`workers/ocr.py:105-107`, `workers/forensics.py:100-106`). The suite proved the *shape* with
fakes; `day7/scripts/cf_commit_order.py` proves it against real Postgres, by replacing
`produce_event` with a checker that opens a **second connection** and reads the row at publish
time. Real Tesseract, real OpenCV scoring; only MinIO (local file) and Kafka (no broker) are
substituted — the two hops the claim is not about.

```
[ocr]       at publish time (ocr_completed):     second connection sees ocr_text=<set, 118 chars>   -> COMMITTED BEFORE PUBLISH: True
[forensics] at publish time (analysis_completed): second connection sees status='completed', score=34.0 -> COMMITTED BEFORE PUBLISH: True
re-delivery: process_ocr -> duplicate; process_forensics -> duplicate
```

That is the property the idempotency rules depend on: a crash between publish and offset commit
redelivers the event, and the redelivery is detected by looking at the committed row.

## 3a. Read cost does not scale with the user's data

The Day-7 N+1 question asks for query-count tests on listing endpoints. CertiFake has no
listing endpoint — nothing iterates rows, so the classic N+1 cannot occur here; the exact
shape of the risk is different, and the test asserts that shape instead
(`test_status_endpoint_statement_count_does_not_grow_with_documents`): `GET /status/{id}`
issues a fixed **2 statements** (one select for the authenticated user by id, one for the
analysis by id + owner) whether the caller owns one analysis or six. The test also asserts the counter fired, because a listener that never
attaches would make the comparison vacuously true.

## 4. Redis — the complete key inventory

| Key | Written by | TTL | Invalidated by |
|---|---|---|---|
| `ratelimit:{scope}` | `app/rate_limit.py:53` (`incr` + `expire` on creation) | yes — the configured window (default 60 s) | expiry only; that is the correct lifetime for a rate-limit counter |

There are no other Redis keys in this project. Pinned by tests in
`tests/test_db_and_cache_day7.py`: the key carries the window as its TTL, a later hit does **not**
extend the window (`expire` is called only when the counter is created), and every key is
namespaced under `ratelimit:`.

## 5. Transactions and pooling

* Session lifecycle: one `SessionLocal()` per message in each worker, committed once at the end
  of the stage, closed in `finally` — the probe above exercises exactly that path against real
  Postgres.
* The workers' redelivery path (SIGKILL mid-message → offset not committed → redelivered →
  duplicate skipped) was verified end-to-end on Day 6 with the real broker
  (`day6/raw/devtrack-*` and `day6/raw/certifake-broker-outage.txt`).
* No connection-pool limit is configured; the API holds one connection per in-flight request
  against Postgres' default 100. Not a finding at this scale, and stated rather than tuned.
