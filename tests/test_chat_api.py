"""Phase 9: API-level tests for document-scoped chat authorization and the
clear-chat/history endpoints (blueprint section 17.5). The query graph itself
(memory loading, standards context, etc.) is covered by test_legal_pipeline_chat.py
against the graph directly; this file covers the API layer's confidentiality
gating and the new GET/POST /api/documents/{id}/chat routes."""

import os
import tempfile
from unittest.mock import patch

import pytest


class _FakeGraphStore:
    """Same role as test_legal_pipeline_chat.py's fake: stands in for
    Neo4jGraphStore so query-graph nodes reached via the real HTTP endpoints
    don't need a live Neo4j connection in these API-level tests."""
    def create_query_job(self, *a, **k): pass
    def write_audit_record(self, *a, **k): pass
    def create_reviewer_decision(self, *a, **k): pass
    def update_job_status(self, *a, **k): pass
    def store_query_answer(self, *a, **k): pass
    def record_answered_question(self, *a, **k): return "answered-1"
    def run_read_query(self, *a, **k): return []


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

    monkeypatch.setattr("counsel_graph.agents.legal_pipeline.get_store", lambda: _FakeGraphStore())

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


def _make_document(**kwargs):
    from counsel_graph.db.repository import create_document
    defaults = dict(filename="test.pdf", status="ready_for_review")
    defaults.update(kwargs)
    return create_document(**defaults)


def test_query_start_404_for_unknown_document(app_client):
    _login(app_client, "junior1")
    resp = app_client.post("/api/query/jobs", json={
        "question": "What is the liability cap?", "collection_name": "x", "document_id": "does-not-exist",
    })
    assert resp.status_code == 404


def test_query_start_blocks_reviewer_from_highly_confidential_document(app_client):
    document_id = _make_document(confidentiality_level="highly_confidential")
    _login(app_client, "junior1")
    resp = app_client.post("/api/query/jobs", json={
        "question": "What is the liability cap?", "collection_name": "x", "document_id": document_id,
    })
    assert resp.status_code == 403


def test_query_start_allows_senior_counsel_on_highly_confidential_document(app_client):
    document_id = _make_document(confidentiality_level="highly_confidential")
    _login(app_client, "senior1")
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=[]), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence",
               return_value={"sufficient": False, "reasoning": "no hits", "gaps": [], "contradictions": []}):
        resp = app_client.post("/api/query/jobs", json={
            "question": "What is the liability cap?", "collection_name": "x", "document_id": document_id,
        })
    assert resp.status_code == 200, resp.text


def test_query_start_works_without_document_id(app_client):
    """document_id is optional -- a caller with no document selected can still ask."""
    _login(app_client, "junior1")
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=[]), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence",
               return_value={"sufficient": False, "reasoning": "no hits", "gaps": [], "contradictions": []}):
        resp = app_client.post("/api/query/jobs", json={
            "question": "What is the liability cap?", "collection_name": "x",
        })
    assert resp.status_code == 200, resp.text


def test_chat_history_endpoint_requires_confidentiality_access(app_client):
    document_id = _make_document(confidentiality_level="highly_confidential")
    _login(app_client, "junior1")
    resp = app_client.get(f"/api/documents/{document_id}/chat")
    assert resp.status_code == 403


def test_chat_history_endpoint_returns_recorded_messages(app_client):
    from counsel_graph.db.repository import record_chat_message

    document_id = _make_document()
    record_chat_message(document_id, "What is the notice period?", "60 days.", "answered", citations=["Clause 5"])
    _login(app_client, "junior1")
    resp = app_client.get(f"/api/documents/{document_id}/chat")
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["answer"] == "60 days."
    assert messages[0]["citations"] == ["Clause 5"]


def test_clear_chat_endpoint_soft_clears(app_client):
    from counsel_graph.db.repository import record_chat_message, get_chat_history

    document_id = _make_document()
    record_chat_message(document_id, "q1", "a1", "answered")
    _login(app_client, "junior1")

    resp = app_client.post(f"/api/documents/{document_id}/chat/clear")
    assert resp.status_code == 200, resp.text

    assert get_chat_history(document_id) == []
    assert len(get_chat_history(document_id, include_cleared=True)) == 1

    # the full-transcript endpoint still shows it (include_cleared=True)
    history_resp = app_client.get(f"/api/documents/{document_id}/chat")
    assert len(history_resp.json()["messages"]) == 1


def test_clear_chat_404_for_unknown_document(app_client):
    _login(app_client, "junior1")
    resp = app_client.post("/api/documents/does-not-exist/chat/clear")
    assert resp.status_code == 404
