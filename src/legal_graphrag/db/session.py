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
from sqlalchemy import text as sa_text
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
    """Creates all tables if they don't already exist, then adds any columns
    that were added to an existing model since the table was first created
    (there's no Alembic here, so create_all alone won't pick those up).
    Idempotent, safe to call on every startup."""
    from sqlalchemy import inspect

    from .models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine, inspect(engine))


def _add_missing_columns(engine, inspector) -> None:
    from .models import Base

    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # brand-new table, create_all already made it with every column
            existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                # existing rows get the column's Python-side default (if any) instead of
                # NULL, so e.g. is_active retrofits to true rather than leaving old rows
                # looking deactivated
                default_sql = ""
                if column.default is not None and column.default.is_scalar:
                    default_sql = f" DEFAULT {column.default.arg!r}" if isinstance(column.default.arg, str) else f" DEFAULT {column.default.arg}"
                conn.execute(sa_text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{default_sql}'))


DEFAULT_CUSTOMER_CODE = os.getenv("DEFAULT_CUSTOMER_CODE", "TATA_GROUP")
DEFAULT_CUSTOMER_NAME = "Tata Group"

_SEED_JURISDICTIONS = [
    # (code, name, legal_system, privacy_regime, data_localization_required)
    ("IN", "India", "Common law", "DPDP Act, 2023", True),
    ("US", "United States", "Common law", "State privacy laws (varies by state)", False),
    ("UK", "United Kingdom", "Common law", "UK GDPR", False),
    ("EU", "European Union", "Civil law (varies by member state)", "GDPR", False),
    ("SG", "Singapore", "Common law", "PDPA", False),
    ("AE", "United Arab Emirates", "Civil law", "UAE PDPL", False),
    ("AU", "Australia", "Common law", "Privacy Act 1988", False),
]


def seed_defaults() -> None:
    """Idempotent seed: one default Customer (Tata Group), the reference
    jurisdiction list, and a backfill of every existing org_profile row onto
    that customer. Safe to call on every startup -- everything here is
    get-or-create by a natural unique key (customer.code, jurisdiction.code),
    never a blind insert."""
    from .models import Customer, Jurisdiction, OrgProfile

    with get_session() as session:
        customer = session.query(Customer).filter_by(code=DEFAULT_CUSTOMER_CODE).one_or_none()
        if customer is None:
            customer = Customer(code=DEFAULT_CUSTOMER_CODE, name=DEFAULT_CUSTOMER_NAME)
            session.add(customer)
            session.flush()

        for code, name, legal_system, privacy_regime, data_localization in _SEED_JURISDICTIONS:
            if session.query(Jurisdiction).filter_by(code=code).one_or_none() is not None:
                continue
            session.add(Jurisdiction(
                code=code, name=name, legal_system=legal_system,
                privacy_regime=privacy_regime, data_localization_required=data_localization,
            ))

        # backfill: every org_profile with no customer_id yet belongs to the
        # default customer (there is exactly one customer today; this is a
        # no-op once every profile has been assigned)
        unassigned = session.query(OrgProfile).filter(OrgProfile.customer_id.is_(None)).all()
        for profile in unassigned:
            profile.customer_id = customer.customer_id


def reset_engine_for_tests() -> None:
    """Test-only: drops the cached engine/session factory so a fresh DATABASE_URL env var takes effect."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
