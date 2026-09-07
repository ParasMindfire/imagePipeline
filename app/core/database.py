"""SQLAlchemy engine/session setup.

Postgres in Docker (DESIGN.md — row-level locking is what makes the
atomic claim UPDATE cheap under N concurrent workers); SQLite is fine
for running tests/ without Docker (DESIGN.md §Assumptions).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

# pool_pre_ping — EDGECASE.md 2.4: a dead connection (Postgres restart,
# network blip) gets detected and quietly replaced on next checkout,
# instead of the next query failing with a stale-connection error.
# pool_size kept small — EDGECASE.md 3.3: N workers × a generous
# default pool can approach Postgres max_connections fast; each worker
# only ever needs ~1 connection at a time (one job at a time,
# DESIGN_QA.md Q10), so 2 is already generous headroom. Not meaningful
# for SQLite (single file, no server-side connection limit to protect).
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=not _is_sqlite,
    pool_size=2 if not _is_sqlite else 5,
    max_overflow=3 if not _is_sqlite else 0,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables directly — used by tests and local dev.

    Production/Docker uses the alembic migration instead (see
    alembic/versions/0001_create_jobs_table.py) so schema changes are
    tracked, not just re-derived from the models on every boot.
    """
    from app.models import job  # noqa: F401 — registers the model on Base.metadata

    Base.metadata.create_all(bind=engine)
