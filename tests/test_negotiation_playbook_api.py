"""Phase 13: API-level tests for the aggregated Negotiation Playbook endpoint
(GET /api/documents/{id}/negotiation-playbook), which rolls up every flagged
clause's playbook entry for a document into one priority-ordered strategy
list. Covers the same confidentiality-gate pattern as document detail
(test_chat_api.py) plus the aggregation/ordering logic itself."""

import os
import tempfile

import pytest


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

    from legal_graphrag.db import session as session_module
    session_module.reset_engine_for_tests()

    import legal_graphrag.api.main as main_module
    monkeypatch.setattr(main_module, "preload_models", lambda **kwargs: None)

    from fastapi.testclient import TestClient
    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        yield c

    session_module.reset_engine_for_tests()
    os.remove(path)


def _login(client, username, password="pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _make_document(**kwargs):
    from legal_graphrag.db.repository import create_document
    defaults = dict(filename="test.pdf", status="ready_for_review")
    defaults.update(kwargs)
    return create_document(**defaults)


def _make_clause(document_id, clause_type, text):
    from legal_graphrag.db.repository import upsert_clause
    clause_id, _created = upsert_clause(document_id=document_id, clause_type=clause_type, extracted_text=text)
    return clause_id


def _make_flagged_clause_with_playbook(document_id, clause_type, text, severity, confidence=0.5,
                                        fallback_source="llm_generated"):
    from legal_graphrag.db.repository import create_risk_flag, create_playbook_entry
    clause_id = _make_clause(document_id, clause_type, text)
    risk_flag_id = create_risk_flag(
        clause_id=clause_id, severity=severity, rationale=f"{clause_type} looks non-standard",
        confidence=confidence, category="non_standard",
    )
    create_playbook_entry(
        risk_flag_id=risk_flag_id, current_language=text,
        fallback_positions=["ideal position", "acceptable fallback"],
        fallback_source=fallback_source, suggested_redline="Replace with approved language.",
    )
    return risk_flag_id


def test_negotiation_playbook_404_for_unknown_document(app_client):
    _login(app_client, "junior1")
    resp = app_client.get("/api/documents/does-not-exist/negotiation-playbook")
    assert resp.status_code == 404


def test_negotiation_playbook_empty_for_document_with_no_flags(app_client):
    document_id = _make_document()
    _login(app_client, "junior1")
    resp = app_client.get(f"/api/documents/{document_id}/negotiation-playbook")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["summary"]["total_entries"] == 0


def test_negotiation_playbook_orders_by_severity_then_confidence(app_client):
    document_id = _make_document()
    _login(app_client, "junior1")

    low_id = _make_flagged_clause_with_playbook(document_id, "termination", "text a", severity="low")
    high_low_conf_id = _make_flagged_clause_with_playbook(document_id, "liability_cap", "text b", severity="high", confidence=0.9)
    high_high_conf_id = _make_flagged_clause_with_playbook(document_id, "indemnification", "text c", severity="high", confidence=0.2)

    resp = app_client.get(f"/api/documents/{document_id}/negotiation-playbook")
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["total_entries"] == 3
    assert body["summary"]["high_severity"] == 2
    assert body["summary"]["llm_sourced"] == 3
    assert body["summary"]["org_profile_sourced"] == 0

    # High severity first; within high severity, lower confidence (less certain -> needs more attention) first.
    ordered_ids = body["priority_order"]
    assert ordered_ids == [high_high_conf_id, high_low_conf_id, low_id]
    assert body["entries"][0]["clause_type"] == "indemnification"
    assert body["entries"][0]["fallback_positions"] == ["ideal position", "acceptable fallback"]
    assert body["entries"][0]["suggested_redline"] == "Replace with approved language."


def test_negotiation_playbook_blocks_reviewer_from_highly_confidential_document(app_client):
    document_id = _make_document(confidentiality_level="highly_confidential")
    _make_flagged_clause_with_playbook(document_id, "termination", "text a", severity="high")
    _login(app_client, "junior1")
    resp = app_client.get(f"/api/documents/{document_id}/negotiation-playbook")
    assert resp.status_code == 403


def test_negotiation_playbook_allows_senior_counsel_on_highly_confidential_document(app_client):
    document_id = _make_document(confidentiality_level="highly_confidential")
    _make_flagged_clause_with_playbook(document_id, "termination", "text a", severity="high")
    _login(app_client, "senior1")
    resp = app_client.get(f"/api/documents/{document_id}/negotiation-playbook")
    assert resp.status_code == 200
    assert resp.json()["summary"]["total_entries"] == 1


def test_negotiation_playbook_includes_document_level_flags(app_client):
    """Document-level risk categories (e.g. missing_clause) have no clause_id --
    these must still surface with clause_type falling back to the flag's category."""
    from legal_graphrag.db.repository import create_risk_flag, create_playbook_entry
    document_id = _make_document()
    risk_flag_id = create_risk_flag(
        clause_id=None, document_id=document_id, severity="medium",
        rationale="missing termination clause", category="missing_clause",
    )
    create_playbook_entry(
        risk_flag_id=risk_flag_id, current_language="(absent)",
        fallback_positions=["add standard termination clause"],
        fallback_source="org_profile", suggested_redline=None,
    )

    _login(app_client, "junior1")
    resp = app_client.get(f"/api/documents/{document_id}/negotiation-playbook")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_entries"] == 1
    assert body["summary"]["org_profile_sourced"] == 1
    assert body["entries"][0]["clause_type"] == "missing_clause"
