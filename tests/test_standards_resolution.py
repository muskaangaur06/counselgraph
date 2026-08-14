"""Phase 5: standards resolution hierarchy (section 11) and cross-tenant
isolation (section 11.3), against a throwaway SQLite DB. No Neo4j/Gemini
needed -- resolve_standards()/find_standards_candidates() are pure DB lookups."""

import os
import tempfile

import pytest


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    from counsel_graph.db import session as session_module
    session_module.reset_engine_for_tests()
    session_module.init_db()

    yield session_module

    session_module.reset_engine_for_tests()
    os.remove(path)


def _make_customer(fresh_db, code="CUST_A", name="Customer A"):
    from counsel_graph.db.models import Customer
    with fresh_db.get_session() as s:
        c = Customer(code=code, name=name)
        s.add(c)
        s.flush()
        return c.customer_id


def _make_org_profile(fresh_db, customer_id, name):
    from counsel_graph.db.models import OrgProfile
    with fresh_db.get_session() as s:
        p = OrgProfile(name=name, customer_id=customer_id)
        s.add(p)
        s.flush()
        return p.profile_id


def _make_business_unit(fresh_db, customer_id, org_profile_id, name):
    from counsel_graph.db.models import BusinessUnit
    with fresh_db.get_session() as s:
        bu = BusinessUnit(customer_id=customer_id, org_profile_id=org_profile_id, name=name)
        s.add(bu)
        s.flush()
        return bu.business_unit_id


def _make_reference(fresh_db, **kwargs):
    from counsel_graph.db.models import KnowledgeReference
    defaults = dict(clause_type="liability", reference_text="default text", source_kind="approved_clause",
                     approval_status="approved", version=1)
    defaults.update(kwargs)
    with fresh_db.get_session() as s:
        r = KnowledgeReference(**defaults)
        s.add(r)
        s.flush()
        return r.knowledge_reference_id


def test_resolves_at_most_specific_available_level(fresh_db):
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    bu_id = _make_business_unit(fresh_db, customer_id, org_id, "BU A")

    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, reference_text="org-level text")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, business_unit_id=bu_id, reference_text="bu-level text")

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=bu_id, jurisdiction_id=None, customer_id=customer_id)
    assert result["scope_level"] == "business_unit_document_type"
    assert result["selected"][0]["reference_text"] == "bu-level text"


def test_falls_back_to_organization_level_without_business_unit_context(fresh_db):
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    bu_id = _make_business_unit(fresh_db, customer_id, org_id, "BU A")

    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, reference_text="org-level text")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, business_unit_id=bu_id, reference_text="bu-level text")

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["scope_level"] == "organization_document_type"
    assert result["selected"][0]["reference_text"] == "org-level text"


def test_tcs_and_tata_steel_resolve_different_standards(fresh_db):
    """Section 11.4 demonstration: same clause_type, different org context ->
    different resolved standard, driven by data, not hardcoded UI text."""
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    tcs_id = _make_org_profile(fresh_db, customer_id, "TCS")
    steel_id = _make_org_profile(fresh_db, customer_id, "Tata Steel")

    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=tcs_id, reference_text="TCS IT-services liability position")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=steel_id, reference_text="Tata Steel manufacturing liability position")

    tcs_result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=tcs_id,
                                    business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    steel_result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=steel_id,
                                      business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)

    assert tcs_result["selected"][0]["reference_text"] != steel_result["selected"][0]["reference_text"]
    assert "TCS" in tcs_result["selected"][0]["reference_text"]
    assert "Steel" in steel_result["selected"][0]["reference_text"]


def test_wrong_customer_retrieval_is_impossible(fresh_db):
    """Section 11.3: never retrieve another customer's standards, even when the
    org_profile_id/business_unit_id happen to collide (shouldn't in practice
    since they're separate UUID spaces, but the customer_id filter must still
    be the deciding boundary, not an afterthought)."""
    from counsel_graph.db.repository import find_standards_candidates
    from counsel_graph.graphrag.standards import resolve_standards

    customer_a = _make_customer(fresh_db, code="CUST_A", name="Customer A")
    customer_b = _make_customer(fresh_db, code="CUST_B", name="Customer B")
    org_a = _make_org_profile(fresh_db, customer_a, "Org A")

    _make_reference(fresh_db, customer_id=customer_b, org_profile_id=org_a, reference_text="Customer B's secret standard")

    candidates = find_standards_candidates(clause_type="liability", document_type=None, org_profile_id=org_a,
                                            business_unit_id=None, jurisdiction_id=None, customer_id=customer_a)
    assert candidates == []

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_a,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_a)
    assert result["selected"] == []
    assert result["scope_level"] is None


def test_customer_group_fallback_visible_across_org_profiles(fresh_db):
    """A KnowledgeReference row with no customer_id at all is a group-wide
    fallback (hierarchy level 8), visible regardless of which customer is asking
    -- this is deliberate (a shared baseline standard), not a tenant-isolation gap."""
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=None, org_profile_id=None, reference_text="group-wide fallback text")

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["scope_level"] == "customer_group_fallback"
    assert result["selected"][0]["reference_text"] == "group-wide fallback text"


def test_conflicting_standards_at_same_level_are_logged_not_merged(fresh_db):
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, reference_text="version 1 text", version=1)
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, reference_text="version 2 text (conflicting)", version=2)

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert len(result["selected"]) == 1
    assert result["selected"][0]["reference_text"] == "version 2 text (conflicting)"
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["reference_text"] == "version 1 text"


def test_expired_standard_excluded_from_resolution(fresh_db):
    from datetime import datetime, timedelta, timezone
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    expired_date = datetime.now(timezone.utc) - timedelta(days=1)
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id,
                     reference_text="expired text", expiry_date=expired_date)

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["selected"] == []
    assert len(result["excluded_inactive"]) == 1


def test_unapproved_standard_excluded_from_resolution(fresh_db):
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id,
                     reference_text="unapproved text", approval_status="unapproved")

    result = resolve_standards(clause_type="liability", document_type=None, org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["selected"] == []


def test_org_wide_standard_applies_to_any_document_type(fresh_db):
    """A KnowledgeReference row with no document_type set is a wildcard -- it
    applies to every document type, not just documents that also have no type."""
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, reference_text="org-wide text", document_type=None)

    result = resolve_standards(clause_type="liability", document_type="service", org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["scope_level"] == "organization_document_type"
    assert result["selected"][0]["reference_text"] == "org-wide text"


def test_document_type_scoped_standard_does_not_leak_to_other_document_types(fresh_db):
    """A row scoped to document_type='supply' must never match a 'service'
    document, at ANY hierarchy level -- not even the unscoped fallback levels."""
    from counsel_graph.graphrag.standards import resolve_standards

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id,
                     reference_text="supply-only text", document_type="supply")

    result = resolve_standards(clause_type="liability", document_type="service", org_profile_id=org_id,
                                business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert result["selected"] == []
    assert result["scope_level"] is None


def test_mandatory_requirement_surfaces_regardless_of_hierarchy_rank(fresh_db):
    from counsel_graph.graphrag.standards import check_mandatory_and_prohibited

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, clause_type="data_protection",
                     reference_text="mandatory data protection clause", is_mandatory=True)

    mp = check_mandatory_and_prohibited(clause_type="data_protection", document_type=None, org_profile_id=org_id,
                                         business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert len(mp["mandatory"]) == 1
    assert mp["prohibited"] == []


def test_prohibited_clause_surfaces(fresh_db):
    from counsel_graph.graphrag.standards import check_mandatory_and_prohibited

    customer_id = _make_customer(fresh_db)
    org_id = _make_org_profile(fresh_db, customer_id, "Org A")
    _make_reference(fresh_db, customer_id=customer_id, org_profile_id=org_id, clause_type="non_compete",
                     reference_text="unlimited non-compete is prohibited", is_prohibited=True)

    mp = check_mandatory_and_prohibited(clause_type="non_compete", document_type=None, org_profile_id=org_id,
                                         business_unit_id=None, jurisdiction_id=None, customer_id=customer_id)
    assert len(mp["prohibited"]) == 1


def test_seed_demo_standards_creates_three_org_profiles_with_distinct_liability_text(fresh_db):
    from counsel_graph.db.session import seed_defaults, seed_demo_standards
    from counsel_graph.db.models import OrgProfile, KnowledgeReference

    seed_defaults()
    seed_demo_standards()
    seed_demo_standards()  # idempotency: must not create duplicates on a second call

    with fresh_db.get_session() as s:
        names = {p.name for p in s.query(OrgProfile).all()}
        assert {"TCS", "Tata Steel", "Tata Motors"} <= names

        liability_refs = s.query(KnowledgeReference).filter_by(clause_type="liability", source_kind="approved_clause").all()
        texts_by_org = {r.org_profile_id: r.reference_text for r in liability_refs}
        assert len(set(texts_by_org.values())) == len(texts_by_org)  # every org's text is distinct

        # idempotency check: exactly one liability reference per demo org, not two
        tcs_id = s.query(OrgProfile).filter_by(name="TCS").one().profile_id
        assert sum(1 for r in liability_refs if r.org_profile_id == tcs_id) == 1
