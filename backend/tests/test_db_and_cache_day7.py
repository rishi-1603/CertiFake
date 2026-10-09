"""Database and cache behaviour, measured (Day 7).

CertiFake's Redis surface is exactly one thing -- the `/analyze` rate-limit counter
(`app/rate_limit.py`) -- so "every key has a TTL" is a short list here, and this file
pins it: the counter's key must carry a TTL, the TTL must be the configured window,
and it must not be extended by later hits (otherwise a busy client's window would
slide forward forever and the limit would never clear).

The Postgres tests below are skipped unless the suite is pointed at a real Postgres
(`DATABASE_URL=postgresql+psycopg2://...`). They exist because two startup claims are
database-specific and cannot be proven on SQLite:

  * `app/models.py` says schema creation is serialised by an advisory lock when
    several processes boot at once, and that it was *reasoned about, never reproduced
    live*. Day 7 reproduced both halves (bare `create_all`: 2-7 of 8 processes died
    per round; locked: 0 of 8). The test keeps the locked property from regressing.
  * Both workers publish their next Kafka event only after the row is committed. The
    existing tests check that shape with fakes; the test here checks it against a real
    database, with a second connection looking at the row at publish time.
"""
import os
import subprocess
import sys

import pytest

import app.rate_limit as rate_limit

# --------------------------------------------------------------------------
# Redis: the one key this project writes
# --------------------------------------------------------------------------

def test_rate_limit_key_carries_the_window_as_its_ttl(_fake_redis):
    allowed, retry_after = rate_limit.check_rate_limit("user-1", limit=3, window_seconds=60)
    assert allowed is True and retry_after == 0

    assert _fake_redis.exists("ratelimit:user-1"), "the counter was not written"
    ttl = _fake_redis.ttl("ratelimit:user-1")
    assert 0 < ttl <= 60, f"counter TTL is {ttl}; it must be the configured window"


def test_rate_limit_ttl_is_not_reset_on_every_hit(_fake_redis):
    """The window is fixed from the first hit, not sliding -- stated, not assumed.

    `rate_limit.py` calls `expire()` only when the counter is created (current == 1).
    """
    rate_limit.check_rate_limit("user-2", limit=10, window_seconds=60)
    _fake_redis.expire("ratelimit:user-2", 42)          # pretend 18 seconds have passed
    for _ in range(5):
        rate_limit.check_rate_limit("user-2", limit=10, window_seconds=60)
    assert _fake_redis.ttl("ratelimit:user-2") <= 42, (
        "a later hit extended the window: the limit would slide forward forever"
    )


def test_rate_limit_blocks_over_the_limit_and_reports_retry_after(_fake_redis):
    for _ in range(3):
        assert rate_limit.check_rate_limit("user-3", limit=3, window_seconds=30)[0] is True
    allowed, retry_after = rate_limit.check_rate_limit("user-3", limit=3, window_seconds=30)
    assert allowed is False and 0 < retry_after <= 30


def test_keys_are_namespaced(_fake_redis):
    """One prefix, so an operator can find (and flush) exactly this app's keys."""
    rate_limit.check_rate_limit("someone", limit=1, window_seconds=10)
    keys = [k.decode() if isinstance(k, bytes) else k for k in _fake_redis.keys("*")]
    assert keys and all(k.startswith("ratelimit:") for k in keys), keys


# --------------------------------------------------------------------------
# Postgres-only: schema serialisation and commit-then-publish ordering
# --------------------------------------------------------------------------

# `tests/conftest.py` forces DATABASE_URL to a throwaway SQLite file when it is
# imported, so the opt-in cannot be read from DATABASE_URL itself. Export
# CERTIFAKE_PG_URL=<dsn> to run the three Postgres tests; the suite's own SQLite
# fixtures are untouched either way.
POSTGRES_URL = os.environ.get("CERTIFAKE_PG_URL", "")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="needs a real Postgres: set CERTIFAKE_PG_URL=postgresql+psycopg2://... "
           "(SQLite cannot show advisory locks or what a second connection can see)",
)

SCRATCH_DB = "certifake_schema_race"


def _base_dsn() -> str:
    return POSTGRES_URL.replace("postgresql://", "postgresql+psycopg2://")


def _scratch_dsn(db: str = SCRATCH_DB) -> str:
    return _base_dsn().rsplit("/", 1)[0] + "/" + db


def _recreate_scratch_db(db: str) -> None:
    import sqlalchemy as sa

    admin = sa.create_engine(_base_dsn().rsplit("/", 1)[0] + "/postgres",
                             isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)"))
        c.execute(sa.text(f"CREATE DATABASE {db}"))
    admin.dispose()


def _run_scenario(db_name: str, body: str) -> str:
    """Run a Postgres scenario in a fresh interpreter and return its stdout.

    A separate process, not a re-import: `app.metrics` registers Prometheus
    collectors in the default registry at import time, so importing the modules a
    second time inside the test process fails with "Duplicated timeseries". A child
    interpreter also gets exactly the environment the scenario is meant to describe --
    a process that boots, talks to Postgres, and exits.
    """
    _recreate_scratch_db(db_name)
    setup = f"""
import os, sys
os.environ['SECRET_KEY'] = 'day7-test-secret-key-0123456789abcdef'
os.environ['DATABASE_URL'] = {_scratch_dsn(db_name)!r}
os.environ.setdefault('KAFKA_BOOTSTRAP_SERVERS', '127.0.0.1:9092')
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r}
)
import app.models as models
models.init_db()
"""
    proc = subprocess.run([sys.executable, "-c", setup + body], capture_output=True, text=True,
                          timeout=180)
    assert proc.returncode == 0, f"scenario failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


@needs_postgres
def test_concurrent_schema_creation_never_raises():
    """Four processes booting against one empty database must all survive.

    Only the safe direction is asserted, so the test cannot be flaky on a slow
    machine: worst case the processes chance to serialise and it passes trivially.
    (The *unsafe* direction -- a bare `create_all`, where 2-7 of 8 processes died per
    round on Day 7 -- is a script, `day7/scripts/cf_lock_race.py`; reproducing a race
    is not something to put a red/green in CI.)
    """
    _recreate_scratch_db(SCRATCH_DB)
    child_code = (
        "import os, sys;"
        "os.environ['SECRET_KEY'] = 'day7-test-secret-key-0123456789abcdef';"
        "os.environ['DATABASE_URL'] = sys.argv[1];"
        "sys.path.insert(0, sys.argv[2]);"
        "from app.models import init_db;"
        "init_db();"
        "print('ok')"
    )
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    procs = [subprocess.Popen([sys.executable, "-c", child_code, _scratch_dsn(), backend],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(4)]
    failures = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        if p.returncode != 0:
            failures.append((err or out).strip().splitlines()[-1] if (err or out).strip() else "exit 1")
    assert not failures, f"schema creation is not serialised: {failures}"


_CERTIFICATE_SCENARIO = """
import tempfile
from PIL import Image, ImageDraw
import sqlalchemy as sa
from app.models import CertificateAnalysis, SessionLocal

tmp = tempfile.mkdtemp(prefix='certifake_scenario_')
png = os.path.join(os.path.dirname(tmp), 'cert.png')
img = Image.new('RGB', (900, 260), 'white')
d = ImageDraw.Draw(img)
d.text((25, 40), 'CERTIFICATE OF COMPLETION', fill='black')
d.text((25, 90), 'Awarded to Jane Doe, 12 May 2026', fill='black')
img.save(png)
png_bytes = open(png, 'rb').read()

import app.workers.ocr as ocr
ocr.download_file_bytes = lambda key: png_bytes
"""


@needs_postgres
def test_worker_commits_before_it_publishes():
    """`process_ocr` must commit the row before `produce_event` is called.

    The publisher is replaced by a checker that opens a *second* connection and looks
    at the row at the moment the event would be published. Real Postgres, real OCR
    (Tesseract); MinIO is replaced by a local file -- the hop the claim is not about.
    """
    body = _CERTIFICATE_SCENARIO + """
seen = {}
def checker(producer, topic, key, value):
    engine = sa.create_engine(os.environ['DATABASE_URL'], isolation_level='AUTOCOMMIT')
    with engine.connect() as c:
        row = c.execute(sa.text('SELECT ocr_text FROM analyses WHERE id = :i'), {'i': key}).first()
    seen['committed'] = bool(row and row[0])
    engine.dispose()
    return True
ocr.produce_event = checker

db = SessionLocal()
db.add(CertificateAnalysis(id='a-commit-order', user_id=None, filename='cert.png',
                           content_type='image/png', status='analyzing'))
db.commit(); db.close()

result = ocr.process_ocr({'analysis_id': 'a-commit-order', 'file_key': 'u/a/cert.png',
                          'content_type': 'image/png'})
print(f'RESULT={result} COMMITTED_BEFORE_PUBLISH={seen.get("committed")}')
"""
    out = _run_scenario("certifake_commit_order", body)
    assert "RESULT=handled COMMITTED_BEFORE_PUBLISH=True" in out, (
        "at publish time the row was not yet visible to another connection:\n" + out
    )


@needs_postgres
def test_redelivered_event_is_skipped():
    """At-least-once delivery: the same `certificate_uploaded` twice must OCR once."""
    body = _CERTIFICATE_SCENARIO + """
published = []
ocr.produce_event = lambda producer, topic, key, value: published.append(topic) or True

db = SessionLocal()
db.add(CertificateAnalysis(id='a-dup', user_id=None, filename='cert.png',
                           content_type='image/png', status='analyzing'))
db.commit(); db.close()

event = {'analysis_id': 'a-dup', 'file_key': 'u/a/cert.png', 'content_type': 'image/png'}
first = ocr.process_ocr(event)
second = ocr.process_ocr(event)
print(f'FIRST={first} SECOND={second} PUBLISHED={published.count("ocr_completed")}')
"""
    out = _run_scenario("certifake_duplicate", body)
    assert "FIRST=handled SECOND=duplicate PUBLISHED=1" in out, out


# --------------------------------------------------------------------------
# Read cost does not scale with how much the user has stored
# --------------------------------------------------------------------------

def test_status_endpoint_statement_count_does_not_grow_with_documents(client, auth_headers):
    """The Day-7 N+1 question, in the shape this API actually has.

    CertiFake has no list endpoint, so the classic N+1 ("one query per row in the
    listing") cannot occur -- there is nothing that iterates rows. What can occur in
    this shape is a per-request cost that grows with the *user's* data: a document
    endpoint that re-reads all of the user's analyses to find one, or a report path
    that queries the row twice. So the assertion is the invariant that matters here:
    a request's statement count is the same whether the user has one analysis or six.
    """
    from sqlalchemy import event

    from app.models import CertificateAnalysis, SessionLocal, engine
    from tests.conftest import TEST_PNG_BYTES

    headers = auth_headers()
    created = client.post("/analyze", headers=headers,
                          files={"file": ("cert.png", TEST_PNG_BYTES, "image/png")})
    assert created.status_code == 202, created.text
    first_id = created.json()["analysis_id"]

    counted = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counted["n"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        counted["n"] = 0
        assert client.get(f"/status/{first_id}", headers=headers).status_code == 200
        with_one = counted["n"]

        # five more analyses for the same user; the same request must not get dearer
        db = SessionLocal()
        user_id = db.get(CertificateAnalysis, first_id).user_id
        for i in range(5):
            db.add(CertificateAnalysis(id=f"a-filler-{i}", user_id=user_id, filename=f"f{i}.png",
                                       content_type="image/png", status="completed"))
        db.commit()
        db.close()

        counted["n"] = 0
        assert client.get(f"/status/{first_id}", headers=headers).status_code == 200
        with_six = counted["n"]
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    # a counter that never fired would make the assertion above vacuous
    assert with_one > 0, "the statement counter never fired -- the listener is not attached"
    assert with_six == with_one, (
        f"the same request cost {with_six} statements with six documents vs {with_one} with one "
        "-- the cost scales with the user's data, which is the N+1 pattern in this API's shape"
    )
