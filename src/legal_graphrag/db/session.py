"""Engine/session helpers for the Postgres operational store.

DATABASE_URL selects the backend. If unset, falls back to a local SQLite file
so tests and local development work without a running Postgres instance;
point DATABASE_URL at a real postgresql+psycopg2:// URL in .env for production
(see .env.example and docker-compose.yml).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .. import config

_engine = None
_SessionLocal = None

_DEFAULT_SQLITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "legal_graphrag.db",
)


def _database_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite:///{_DEFAULT_SQLITE_PATH}"


def get_engine():
    global _engine
    if _engine is None:
        url = _database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Creates all tables if they don't already exist. Idempotent, safe to call on every startup."""
    from .models import Base
    Base.metadata.create_all(get_engine())


def reset_engine_for_tests() -> None:
    """Test-only: drops the cached engine/session factory so a fresh DATABASE_URL env var takes effect."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
