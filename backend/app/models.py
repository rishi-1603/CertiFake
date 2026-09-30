import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

# Advisory-lock key used by init_db() to serialize concurrent schema creation
# across the three containers that import this module. Any fixed 64-bit integer
# works; it just has to be identical everywhere and unlikely to collide with
# another advisory-lock consumer sharing the same database.
_SCHEMA_LOCK_KEY = 740_202_601

# No default on purpose (Day-7 remediation, finding S8). This used to fall back
# to "sqlite:///./certifake.db", so a production container started without
# DATABASE_URL did not fail -- it quietly ran on a file-backed SQLite inside the
# container, losing every row on restart and silently diverging from the
# Postgres the rest of the stack was using. A missing database is a
# misconfiguration, not a condition to paper over.
#
# Every real path sets it: docker-compose.yml sets it on the API and both
# workers, backend/.env.example documents it, and tests/conftest.py points it at
# a throwaway temp file before importing app/.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Refusing to start: without it this process "
        "would fall back to a file-backed SQLite database and silently lose "
        "data on restart. Set it to the Postgres DSN "
        "(see backend/.env.example), e.g. "
        "postgresql://certifake:password@postgres:5432/certifake_db"
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    analyses = relationship("CertificateAnalysis", back_populates="owner")


class CertificateAnalysis(Base):
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, index=True)
    # Nullable ONLY to keep the schema valid for rows created before auth was
    # reinstated; every new row created through the API always sets this via
    # the authenticated user's id. See app/api.py:analyze() and
    # app/auth.py:require_user() for how ownership is enforced.
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    filename = Column(String)
    content_type = Column(String)
    status = Column(String, default="pending")  # pending, analyzing, completed, failed

    # OCR Results
    ocr_text = Column(String, nullable=True)
    extracted_fields = Column(JSON, nullable=True)

    # Forensic Results
    authenticity_score = Column(Float, nullable=True)
    verdict = Column(String, nullable=True)
    suspicious_signals = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)

    # Output
    report_url = Column(String, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    owner = relationship("User", back_populates="analyses")


def init_db() -> None:
    """Create any missing tables, serialized across processes.

    WHY THIS IS NOT A BARE `Base.metadata.create_all(bind=engine)`:

    Three containers run this codebase (api-gateway, worker-ocr and
    worker-forensics are one image with three different commands), and all
    three import this module at startup -- so on a fresh database all three
    call create_all() at roughly the same moment.

    create_all(checkfirst=True) is NOT `CREATE TABLE IF NOT EXISTS`. It
    reflects over the database to find which tables are missing and then emits
    a plain `CREATE TABLE` for each. Two processes can therefore both observe
    "no tables yet", both emit CREATE TABLE, and the loser dies with
    sqlstate 42P07 (duplicate_table) -- during import, before it can serve or
    consume anything.

    `restart: unless-stopped` in docker-compose.yml would usually paper over
    this (the crashed container restarts and finds the tables already there),
    which is exactly why it cannot be relied on: it turns a deterministic
    startup into a race whose outcome depends on container scheduling, and
    `docker compose up --wait` treats an exited container as a failure.

    A Postgres session-level advisory lock serializes the creators instead:
    the first process builds the schema, the others block on the lock and then
    find the tables already present. The key is an arbitrary fixed constant --
    it only has to be the same in every process, and unlikely to collide with
    another advisory-lock user in the same database (this app is the only one).

    Not observed in a live run when written: no Docker daemon was available in
    the sandbox this was developed in, so the race is reasoned about from the
    code and SQLAlchemy's documented checkfirst behaviour rather than
    reproduced. It is fixed here because it is a startup-ordering defect that
    would otherwise only ever appear as an intermittent boot failure.

    SQLite (used by the test suite) has no advisory locks and no concurrent
    writers here, so it takes the plain path.
    """
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
            try:
                Base.metadata.create_all(bind=engine)
            finally:
                # Released explicitly rather than relying on connection close:
                # SQLAlchemy returns this connection to the pool for reuse, and
                # a session-level advisory lock would otherwise stay held by
                # whichever process picked that pooled connection up next.
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})
    else:
        Base.metadata.create_all(bind=engine)


init_db()
