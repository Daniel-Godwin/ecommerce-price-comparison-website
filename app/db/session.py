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
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


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
