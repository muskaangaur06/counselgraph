"""Phase 4: API-level tests for confidentiality override (section 12.3) and
access-control integration (section 12.4). Same throwaway-SQLite pattern as
test_review_action_authorization.py, no Neo4j/Gemini needed."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


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

    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        yield c

    session_module.reset_engine_for_tests()
    os.remove(path)


def _login(client, username, password="pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _make_document(confidentiality_level=None):
    from legal_graphrag.db.repository import create_document
    return create_document(filename="test.pdf", status="ready_for_review", confidentiality_level=confidentiality_level)


def test_senior_counsel_can_override_confidentiality(app_client):
    _login(app_client, "senior1")
    document_id = _make_document(confidentiality_level="internal")

    resp = app_client.post(
        f"/api/documents/{document_id}/confidentiality",
        json={"new_level": "highly_confidential", "reason": "Contains board-level valuation data."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["previous_level"] == "internal"
    assert body["new_level"] == "highly_confidential"


def test_reviewer_cannot_override_confidentiality(app_client):
    _login(app_client, "junior1")
    document_id = _make_document(confidentiality_level="internal")

    resp = app_client.post(
        f"/api/documents/{document_id}/confidentiality",
        json={"new_level": "highly_confidential", "reason": "Trying to escalate."},
    )
    assert resp.status_code == 403


def test_override_writes_audit_record(app_client):
    _login(app_client, "admin1")
    document_id = _make_document(confidentiality_level="internal")

    app_client.post(
        f"/api/documents/{document_id}/confidentiality",
        json={"new_level": "confidential", "reason": "Contains pricing schedule."},
    )

    from legal_graphrag.db.repository import get_confidentiality_history, get_audit_log
    history = get_confidentiality_history(document_id)
    assert len(history) == 1
    assert history[0]["source"] == "manual_override"
    assert history[0]["changed_by"] == "admin1"

    audit = get_audit_log(document_id)
    assert any(a["stage"] == "confidentiality" for a in audit)


def test_override_requires_reason(app_client):
    _login(app_client, "admin1")
    document_id = _make_document(confidentiality_level="internal")

    resp = app_client.post(
        f"/api/documents/{document_id}/confidentiality",
        json={"new_level": "confidential", "reason": ""},
    )
    assert resp.status_code == 422


def test_access_control_blocks_reviewer_from_highly_confidential_document(app_client):
    _login(app_client, "junior1")
    document_id = _make_document(confidentiality_level="highly_confidential")

    resp = app_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 403


def test_access_control_allows_senior_counsel_on_highly_confidential_document(app_client):
    _login(app_client, "senior1")
    document_id = _make_document(confidentiality_level="highly_confidential")

    resp = app_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 200


def test_access_control_allows_reviewer_on_internal_document(app_client):
    _login(app_client, "junior1")
    document_id = _make_document(confidentiality_level="internal")

    resp = app_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 200


def test_access_control_changes_after_override(app_client):
    _login(app_client, "admin1")
    document_id = _make_document(confidentiality_level="internal")

    resp = app_client.get(f"/api/documents/{document_id}")
    assert resp.status_code == 200

    app_client.post(
        f"/api/documents/{document_id}/confidentiality",
        json={"new_level": "highly_confidential", "reason": "Escalating after review."},
    )

    from legal_graphrag.api.security import create_session_token
    client_junior = TestClient(app_client.app)
    client_junior.cookies.set("lg_session", create_session_token("junior1", "reviewer"))
    resp = client_junior.get(f"/api/documents/{document_id}")
    assert resp.status_code == 403
