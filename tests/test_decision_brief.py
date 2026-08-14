"""Phase 8: Decision Brief and approval handoff (blueprint section 15).
Repository/service-level tests against a throwaway SQLite DB plus a mocked
Gemini call, and API-level tests for the approval-chain decision flow -- same
pattern as test_summary_versioning.py / test_confidentiality_override.py."""

import os
import tempfile
from unittest.mock import patch

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


def _make_document(**fields):
    from counsel_graph.db.repository import create_document
    defaults = {"filename": "test.pdf", "status": "ready_for_review"}
    defaults.update(fields)
    return create_document(**defaults)


# ---------------------------------------------------------------------------
# resolve_approval_chain (pure, no LLM/DB)
# ---------------------------------------------------------------------------

def test_default_chain_is_reviewer_then_senior_counsel():
    from counsel_graph.graphrag.decision_brief import resolve_approval_chain

    chain = resolve_approval_chain(
        {"monetary_value": None, "confidentiality_level": "internal"}, [], [], {},
    )
    assert [s["required_role"] for s in chain] == ["reviewer", "senior_counsel"]


def test_high_severity_risk_adds_business_head():
    from counsel_graph.graphrag.decision_brief import resolve_approval_chain

    chain = resolve_approval_chain(
        {"monetary_value": None, "confidentiality_level": "internal"},
        [{"severity": "high"}], [], {},
    )
    assert "business_head" in [s["required_role"] for s in chain]


def test_over_value_threshold_adds_business_head():
    from counsel_graph.graphrag.decision_brief import resolve_approval_chain, DEFAULT_VALUE_THRESHOLD

    chain = resolve_approval_chain(
        {"monetary_value": DEFAULT_VALUE_THRESHOLD + 1, "confidentiality_level": "internal"}, [], [], {},
    )
    assert "business_head" in [s["required_role"] for s in chain]


def test_highly_confidential_adds_clo():
    from counsel_graph.graphrag.decision_brief import resolve_approval_chain

    chain = resolve_approval_chain(
        {"monetary_value": None, "confidentiality_level": "highly_confidential"}, [], [], {},
    )
    assert "clo" in [s["required_role"] for s in chain]


def test_configured_org_policy_overrides_default_chain():
    """Section 15.5: 'do not hardcode the contract-value threshold globally if
    an organization policy exists' -- a configured approval_policy is used
    verbatim, even for a low-value, low-risk, low-confidentiality document that
    would otherwise get just the 2-step default chain."""
    from counsel_graph.graphrag.decision_brief import resolve_approval_chain

    overrides = {"approval_policy": [{"role": "clo", "reason": "org always requires CLO"}]}
    chain = resolve_approval_chain(
        {"monetary_value": None, "confidentiality_level": "internal"}, [], [], overrides,
    )
    assert [s["required_role"] for s in chain] == ["clo"]


# ---------------------------------------------------------------------------
# evidence_validate_sections
# ---------------------------------------------------------------------------

def test_evidence_validation_passes_when_figures_match():
    from counsel_graph.graphrag.decision_brief import evidence_validate_sections

    sections = {"financial_terms": "The contract value is 5,000,000."}
    brief_input = {"financial_terms": {"monetary_value": 5000000}}
    validated, unsupported = evidence_validate_sections(sections, brief_input)
    assert validated is True
    assert unsupported == []


def test_evidence_validation_flags_unsupported_figure():
    from counsel_graph.graphrag.decision_brief import evidence_validate_sections

    sections = {"financial_terms": "The contract value is 9,999,999."}
    brief_input = {"financial_terms": {"monetary_value": 5000000}}
    validated, unsupported = evidence_validate_sections(sections, brief_input)
    assert validated is False
    assert len(unsupported) == 1


# ---------------------------------------------------------------------------
# generate_decision_brief_sections (mocked LLM call)
# ---------------------------------------------------------------------------

def test_generate_brief_falls_back_safely_when_llm_fails():
    from counsel_graph.graphrag.decision_brief import generate_decision_brief_sections

    with patch("counsel_graph.graphrag.decision_brief.call_json", side_effect=RuntimeError("boom")):
        result = generate_decision_brief_sections(
            {"filename": "t.pdf"}, [], [], None, [], [], [],
        )
    assert result["recommendation"] == "escalate"
    assert result["evidence_validated"] is True  # fallback text has no invented figures
    assert "Brief generation failed" in result["sections"]["executive_summary"]


def test_generate_brief_uses_llm_result_when_valid():
    from counsel_graph.graphrag.decision_brief import generate_decision_brief_sections, BRIEF_SECTIONS

    fake_response = {s: "n/a" for s in BRIEF_SECTIONS}
    fake_response["first_reviewer_recommendation"] = {"recommendation": "approve", "rationale": "clean review"}
    fake_response["ai_recommendation"] = {"recommendation": "approve", "rationale": "no material risk"}

    with patch("counsel_graph.graphrag.decision_brief.call_json", return_value=fake_response):
        result = generate_decision_brief_sections(
            {"filename": "t.pdf"}, [], [], None, [{"action": "accept"}], [], [],
        )
    assert result["recommendation"] == "approve"
    assert result["sections"]["executive_summary"] == "n/a"


# ---------------------------------------------------------------------------
# Repository: create_decision_brief / approval steps (real SQLite, no LLM)
# ---------------------------------------------------------------------------

def test_create_decision_brief_persists_versioned_brief_and_chain(fresh_db):
    from counsel_graph.db.repository import create_decision_brief, get_latest_decision_brief

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "first reviewer"},
             {"required_role": "senior_counsel", "reason": "second-level"}]
    brief = create_decision_brief(document_id, "admin", {"executive_summary": "x"}, "approve", True, [], chain)

    assert brief["version_number"] == 1
    assert len(brief["approval_steps"]) == 2
    assert brief["approval_steps"][0]["status"] == "pending"

    latest = get_latest_decision_brief(document_id)
    assert latest["decision_brief_id"] == brief["decision_brief_id"]


def test_second_brief_is_a_new_version_not_an_overwrite(fresh_db):
    from counsel_graph.db.repository import create_decision_brief, get_decision_brief_history

    document_id = _make_document()
    create_decision_brief(document_id, "admin", {"executive_summary": "v1"}, "escalate", True, [], [])
    create_decision_brief(document_id, "admin", {"executive_summary": "v2"}, "approve", True, [], [])

    history = get_decision_brief_history(document_id)
    assert len(history) == 2
    assert history[0]["sections"]["executive_summary"] == "v1"
    assert history[1]["sections"]["executive_summary"] == "v2"


def test_get_next_pending_approval_step_returns_first_in_sequence(fresh_db):
    from counsel_graph.db.repository import create_decision_brief, get_next_pending_approval_step

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "r1"}, {"required_role": "senior_counsel", "reason": "r2"}]
    brief = create_decision_brief(document_id, "admin", {}, "approve", True, [], chain)

    step = get_next_pending_approval_step(brief["decision_brief_id"])
    assert step["required_role"] == "reviewer"


def test_decide_approval_step_advances_to_next_step(fresh_db):
    from counsel_graph.db.repository import (
        create_decision_brief, get_next_pending_approval_step, decide_approval_step, get_decision_brief,
    )

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "r1"}, {"required_role": "senior_counsel", "reason": "r2"}]
    brief = create_decision_brief(document_id, "admin", {}, "approve", True, [], chain)

    first_step = get_next_pending_approval_step(brief["decision_brief_id"])
    decide_approval_step(first_step["approval_step_id"], "reviewer1", "approve")

    second_step = get_next_pending_approval_step(brief["decision_brief_id"])
    assert second_step["required_role"] == "senior_counsel"

    reloaded = get_decision_brief(brief["decision_brief_id"])
    assert reloaded["status"] == "pending"  # not every step decided yet


def test_last_step_approval_marks_brief_approved(fresh_db):
    from counsel_graph.db.repository import (
        create_decision_brief, get_next_pending_approval_step, decide_approval_step, get_decision_brief,
    )

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "r1"}]
    brief = create_decision_brief(document_id, "admin", {}, "approve", True, [], chain)

    step = get_next_pending_approval_step(brief["decision_brief_id"])
    decide_approval_step(step["approval_step_id"], "reviewer1", "approve")

    reloaded = get_decision_brief(brief["decision_brief_id"])
    assert reloaded["status"] == "approve"
    assert get_next_pending_approval_step(brief["decision_brief_id"]) is None


def test_reject_marks_brief_rejected_immediately(fresh_db):
    from counsel_graph.db.repository import (
        create_decision_brief, get_next_pending_approval_step, decide_approval_step, get_decision_brief,
    )

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "r1"}, {"required_role": "senior_counsel", "reason": "r2"}]
    brief = create_decision_brief(document_id, "admin", {}, "approve", True, [], chain)

    step = get_next_pending_approval_step(brief["decision_brief_id"])
    decide_approval_step(step["approval_step_id"], "reviewer1", "reject", comments="not acceptable")

    reloaded = get_decision_brief(brief["decision_brief_id"])
    assert reloaded["status"] == "reject"
    # rejecting stops the chain -- the second step never becomes current
    assert get_next_pending_approval_step(brief["decision_brief_id"]) is None


def test_decide_already_decided_step_raises(fresh_db):
    from counsel_graph.db.repository import create_decision_brief, get_next_pending_approval_step, decide_approval_step

    document_id = _make_document()
    chain = [{"required_role": "reviewer", "reason": "r1"}]
    brief = create_decision_brief(document_id, "admin", {}, "approve", True, [], chain)
    step = get_next_pending_approval_step(brief["decision_brief_id"])
    decide_approval_step(step["approval_step_id"], "reviewer1", "approve")

    with pytest.raises(ValueError):
        decide_approval_step(step["approval_step_id"], "reviewer1", "approve")


# ---------------------------------------------------------------------------
# API-level: generation gated on review completeness, decision routing/role checks
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setenv("REVIEWERS", (
        '[{"username":"admin1","password":"pw","role":"admin"},'
        '{"username":"junior1","password":"pw","role":"reviewer"},'
        '{"username":"senior1","password":"pw","role":"senior_counsel"}]'
    ))

    from counsel_graph.db import session as session_module
    session_module.reset_engine_for_tests()

    import counsel_graph.api.main as main_module
    monkeypatch.setattr(main_module, "preload_models", lambda **kwargs: None)

    from fastapi.testclient import TestClient
    from counsel_graph.api.main import app
    with TestClient(app) as c:
        yield c

    session_module.reset_engine_for_tests()
    os.remove(path)


def _login(client, username, password="pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def test_generate_brief_rejects_when_review_incomplete(app_client):
    document_id = _make_document()
    _login(app_client, "junior1")
    resp = app_client.post(f"/api/documents/{document_id}/decision-brief")
    assert resp.status_code == 400


def test_generate_brief_succeeds_after_review_action_recorded(app_client):
    from counsel_graph.db.repository import record_review_action

    document_id = _make_document()
    record_review_action(reviewer_username="junior1", role="reviewer", action="accept", document_id=document_id)
    _login(app_client, "junior1")

    with patch("counsel_graph.graphrag.decision_brief.call_json", side_effect=RuntimeError("no live LLM in tests")):
        resp = app_client.post(f"/api/documents/{document_id}/decision-brief")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version_number"] == 1
    assert len(body["approval_steps"]) >= 1


def test_junior_reviewer_cannot_decide_senior_counsel_step(app_client):
    from counsel_graph.db.repository import record_review_action

    document_id = _make_document()
    record_review_action(reviewer_username="junior1", role="reviewer", action="accept", document_id=document_id)
    _login(app_client, "junior1")

    with patch("counsel_graph.graphrag.decision_brief.call_json", side_effect=RuntimeError("no live LLM in tests")):
        gen_resp = app_client.post(f"/api/documents/{document_id}/decision-brief")
    brief = gen_resp.json()

    reviewer_step = brief["approval_steps"][0]
    resp = app_client.post(
        f"/api/decision-briefs/{brief['decision_brief_id']}/approval-steps/{reviewer_step['approval_step_id']}/decide",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200, resp.text

    senior_step = brief["approval_steps"][1]
    resp = app_client.post(
        f"/api/decision-briefs/{brief['decision_brief_id']}/approval-steps/{senior_step['approval_step_id']}/decide",
        json={"decision": "approve"},
    )
    assert resp.status_code == 403


def test_reject_without_comments_is_rejected_by_api(app_client):
    from counsel_graph.db.repository import record_review_action

    document_id = _make_document()
    record_review_action(reviewer_username="junior1", role="reviewer", action="accept", document_id=document_id)
    _login(app_client, "junior1")

    with patch("counsel_graph.graphrag.decision_brief.call_json", side_effect=RuntimeError("no live LLM in tests")):
        gen_resp = app_client.post(f"/api/documents/{document_id}/decision-brief")
    brief = gen_resp.json()
    reviewer_step = brief["approval_steps"][0]

    resp = app_client.post(
        f"/api/decision-briefs/{brief['decision_brief_id']}/approval-steps/{reviewer_step['approval_step_id']}/decide",
        json={"decision": "reject"},
    )
    assert resp.status_code == 400
