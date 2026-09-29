"""Tests for app/models.py:init_db() -- schema creation that is safe when
three containers do it at once.

WHY THESE EXIST
---------------
api-gateway, worker-ocr and worker-forensics are one image with three commands,
and all three import app.models at startup. On a fresh Postgres they therefore
all call create_all() concurrently, and create_all(checkfirst=True) emits a
plain `CREATE TABLE` (not `IF NOT EXISTS`) after a reflect-then-create check --
so two processes can both see "missing" and the loser dies with sqlstate 42P07
during import. init_db() serializes them with a session-level advisory lock.

WHAT CAN AND CANNOT BE PROVEN HERE
----------------------------------
The SQLite branch is exercised for real against a throwaway database.

The Postgres branch CANNOT be exercised for real in this suite: the tests run
on SQLite precisely so they need no Postgres/Kafka/MinIO (see conftest.py), and
advisory locks are a Postgres-specific feature with no SQLite equivalent. So
instead these tests pin the branch's observable CONTRACT with a fake engine --
that it takes the lock, creates the schema, and releases the lock, in that
order, with the same key both times, and that the release still happens when
schema creation raises. That last one is the part worth testing: the lock is
session-level, and this code runs on a POOLED connection, so a missed unlock
would hand the lock to whichever process reuses that connection next -- a
deadlock that would not reproduce in a single-process test run.

What is NOT verified by any test, and is stated rather than implied: that a
real Postgres actually blocks the second process. That requires two live
connections and a real server; the compose-smoke-test CI job boots the real
stack, which is the closest thing to covering it.
"""
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect

import app.models as models

EXPECTED_TABLES = {"users", "analyses"}


# ---------------------------------------------------------------------------
# SQLite branch: exercised for real
# ---------------------------------------------------------------------------
def test_init_db_creates_missing_tables_on_a_fresh_database(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(models, "engine", engine)

    # Precondition matters: if the tables already existed, the test would pass
    # even if init_db() did nothing at all.
    assert inspect(engine).get_table_names() == []

    models.init_db()

    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_init_db_is_idempotent(monkeypatch, tmp_path):
    """Second call must be a no-op, not an error.

    This is the property the advisory lock exists to protect in the concurrent
    case; asserting it sequentially at least pins that calling init_db() twice
    (which happens: once at import of app.models, once explicitly) is safe.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(models, "engine", engine)

    models.init_db()
    first = set(inspect(engine).get_table_names())
    models.init_db()
    second = set(inspect(engine).get_table_names())

    assert first == second
    assert EXPECTED_TABLES <= second


def test_sqlite_branch_does_not_attempt_an_advisory_lock(monkeypatch, tmp_path):
    """SQLite has no pg_advisory_lock; taking that branch would raise."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(models, "engine", engine)
    assert engine.dialect.name == "sqlite"

    models.init_db()  # would blow up with an OperationalError if it tried


# ---------------------------------------------------------------------------
# Postgres branch: contract pinned with a fake engine (see module docstring)
# ---------------------------------------------------------------------------
class _RecordingConnection:
    def __init__(self, log):
        self._log = log

    def execute(self, statement, params=None):
        self._log.append(("sql", str(statement), params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePostgresEngine:
    """Only what init_db() touches: .dialect.name and .connect()."""

    def __init__(self, log):
        self._log = log
        self.dialect = SimpleNamespace(name="postgresql")

    def connect(self):
        return _RecordingConnection(self._log)


def _patch_postgres(monkeypatch, create_all_raises=False):
    log: list = []
    monkeypatch.setattr(models, "engine", _FakePostgresEngine(log))

    def fake_create_all(*args, **kwargs):
        log.append(("create_all",))
        if create_all_raises:
            raise RuntimeError("simulated CREATE TABLE failure (e.g. 42P07)")

    monkeypatch.setattr(models.Base.metadata, "create_all", fake_create_all)
    return log


def test_postgres_branch_locks_creates_then_unlocks_in_order(monkeypatch):
    log = _patch_postgres(monkeypatch)

    models.init_db()

    kinds = [entry[0] for entry in log]
    assert kinds == ["sql", "create_all", "sql"], (
        f"expected lock -> create -> unlock, got {kinds}"
    )
    assert "pg_advisory_lock" in log[0][1]
    assert "pg_advisory_unlock" in log[2][1]


def test_lock_and_unlock_use_the_same_key(monkeypatch):
    """An unlock with a different key silently does nothing, leaving the lock
    held for the lifetime of the session -- so the keys are asserted, not
    assumed."""
    log = _patch_postgres(monkeypatch)

    models.init_db()

    assert log[0][2] == {"k": models._SCHEMA_LOCK_KEY}
    assert log[2][2] == {"k": models._SCHEMA_LOCK_KEY}
    assert log[0][2] == log[2][2]


def test_unlock_still_runs_when_schema_creation_fails(monkeypatch):
    """The `finally` is load-bearing: without it a failed create_all leaks a
    session-level advisory lock on a connection that goes back to the pool, and
    the next process to borrow that connection is already holding a lock it
    never asked for -- a deadlock no single-process test would ever reproduce.
    """
    log = _patch_postgres(monkeypatch, create_all_raises=True)

    with_error = None
    try:
        models.init_db()
    except RuntimeError as exc:
        with_error = exc

    assert with_error is not None, "init_db() must not swallow a create_all failure"
    assert [entry[0] for entry in log] == ["sql", "create_all", "sql"]
    assert "pg_advisory_unlock" in log[2][1]
