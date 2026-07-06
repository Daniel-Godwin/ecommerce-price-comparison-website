"""Engine/session factory. SQLite in dev, PostgreSQL via DATABASE_URL."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_DEFAULT_URL = "sqlite:///./pricecompare.db"


def _make_engine():
    url = os.getenv("DATABASE_URL", _DEFAULT_URL)
    # Render/Heroku-style URLs (postgres:// or postgresql://) make SQLAlchemy
    # look for the legacy psycopg2 driver; point it at psycopg v3 explicitly.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    
    if url.startswith("sqlite"):
        # timeout: wait for locks instead of failing; WAL: allow a reader
        # and a writer concurrently (llm_calls logging happens on a second
        # connection while a request session is open)
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )
        from sqlalchemy import event

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return eng
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if absent. (Alembic migrations arrive once the
    schema stabilizes; create_all is sufficient for Phase 2 dev.)"""
    Base.metadata.create_all(engine)


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with db_session() as session:
        yield session
