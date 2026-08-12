"""Phase 1 tenancy schema: init_db()'s column-retrofit path and seed_defaults()'s
Customer/Jurisdiction seed + org_profile backfill, against a throwaway SQLite DB
so this doesn't touch the real Postgres data."""

import os
import tempfile

import pytest


@pytest.fixture
def fresh_db(monkeypatch):
    """Points DATABASE_URL at a throwaway SQLite file and resets the cached
    engine so session.py picks it up, then pre-creates one org_profile row
    with no customer_id, mimicking the pre-migration state of the real DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    from legal_graphrag.db import session as session_module
    session_module.reset_engine_for_tests()

    yield session_module

    session_module.reset_engine_for_tests()
    os.remove(path)


def test_init_db_creates_tenancy_tables(fresh_db):
    from sqlalchemy import inspect

    fresh_db.init_db()
    tables = set(inspect(fresh_db.get_engine()).get_table_names())
    assert {"customer", "jurisdiction", "business_unit"} <= tables


def test_init_db_retrofits_columns_onto_existing_org_profile(fresh_db):
    from legal_graphrag.db.models import OrgProfile

    fresh_db.init_db()
    with fresh_db.get_session() as s:
        s.add(OrgProfile(name="Pre-existing Profile"))
    fresh_db.init_db()  # second call must not error on already-added columns

    with fresh_db.get_session() as s:
        profile = s.query(OrgProfile).filter_by(name="Pre-existing Profile").one()
        assert profile.is_active is True
        assert profile.customer_id is None


def test_seed_defaults_creates_one_customer_and_backfills_org_profiles(fresh_db):
    from legal_graphrag.db.models import Customer, OrgProfile

    fresh_db.init_db()
    with fresh_db.get_session() as s:
        s.add(OrgProfile(name="Vendor Procurement Unit"))
        s.add(OrgProfile(name="General Counsel Office"))

    fresh_db.seed_defaults()

    with fresh_db.get_session() as s:
        customers = s.query(Customer).all()
        assert len(customers) == 1
        assert customers[0].code == fresh_db.DEFAULT_CUSTOMER_CODE

        profiles = s.query(OrgProfile).all()
        assert len(profiles) == 2
        assert all(p.customer_id == customers[0].customer_id for p in profiles)


def test_seed_defaults_is_idempotent(fresh_db):
    from legal_graphrag.db.models import Customer, Jurisdiction

    fresh_db.init_db()
    fresh_db.seed_defaults()
    fresh_db.seed_defaults()  # must not create a second customer or duplicate jurisdictions

    with fresh_db.get_session() as s:
        assert s.query(Customer).count() == 1
        assert s.query(Jurisdiction).count() == len(fresh_db._SEED_JURISDICTIONS)


def test_seed_defaults_does_not_reassign_already_scoped_org_profile(fresh_db):
    """An org_profile already assigned to a (future, hypothetical) different
    customer must not be silently reassigned to the default customer on a
    later seed_defaults() call."""
    from legal_graphrag.db.models import Customer, OrgProfile

    fresh_db.init_db()
    with fresh_db.get_session() as s:
        other_customer = Customer(code="OTHER_CUSTOMER", name="Other Customer")
        s.add(other_customer)
        s.flush()
        s.add(OrgProfile(name="Scoped Elsewhere", customer_id=other_customer.customer_id))
        other_customer_id = other_customer.customer_id

    fresh_db.seed_defaults()

    with fresh_db.get_session() as s:
        profile = s.query(OrgProfile).filter_by(name="Scoped Elsewhere").one()
        assert profile.customer_id == other_customer_id
