"""Phase 2: a risk flag routed to senior_counsel (langgraph_agent.py's
risk-based routing) must not be actionable by a plain reviewer over the API.
Runs against a throwaway SQLite DB, no Neo4j/Gemini needed."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    # .env sets NEO4J_URI to the container-only "neo4j" hostname; unset it here so
    # get_store() fails fast on a missing env var instead of retrying a slow DNS
    # resolution against a host that's unreachable from outside the Docker network
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setenv("REVIEWERS", (
        '[{"username":"admin1","password":"pw","role":"admin"},'
        '{"username":"junior1","password":"pw","role":"reviewer"},'
        '{"username":"senior1","password":"pw","role":"senior_counsel"}]'
    ))

    from counsel_graph.db import session as session_module
    session_module.reset_engine_for_tests()

    # /api/review-actions never touches the embedder/reranker; skip loading them
    # so this test doesn't need the (memory-heavy, slow-on-this-host) real models
    import counsel_graph.api.main as main_module
    monkeypatch.setattr(main_module, "preload_models", lambda **kwargs: None)

    from counsel_graph.api.main import app
    with TestClient(app) as c:
        yield c

    session_module.reset_engine_for_tests()
    os.remove(path)


def _login(client, username, password="pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def _make_senior_counsel_risk_flag(client) -> str:
    """Creates a clause + risk flag directly via the repository layer (no
    document-processing pipeline needed) with assigned_role=senior_counsel."""
    from counsel_graph.db.repository import create_document, create_risk_flag, upsert_clause

    document_id = create_document(filename="test.pdf", status="ready_for_review")
    clause_id, _ = upsert_clause(document_id=document_id, clause_type="liability", extracted_text="Unlimited liability clause.")
    return create_risk_flag(clause_id=clause_id, severity="critical", assigned_role="senior_counsel")


def test_reviewer_cannot_act_on_senior_counsel_routed_flag(app_client):
    _login(app_client, "junior1")
    risk_flag_id = _make_senior_counsel_risk_flag(app_client)

    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": risk_flag_id})
    assert resp.status_code == 403
    assert "senior_counsel" in resp.json()["detail"]


def test_senior_counsel_can_act_on_senior_counsel_routed_flag(app_client):
    _login(app_client, "senior1")
    risk_flag_id = _make_senior_counsel_risk_flag(app_client)

    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": risk_flag_id})
    assert resp.status_code == 200, resp.text


def test_admin_can_act_on_senior_counsel_routed_flag(app_client):
    _login(app_client, "admin1")
    risk_flag_id = _make_senior_counsel_risk_flag(app_client)

    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": risk_flag_id})
    assert resp.status_code == 200, resp.text


def test_reviewer_can_act_on_reviewer_routed_flag(app_client):
    _login(app_client, "junior1")
    from counsel_graph.db.repository import create_document, create_risk_flag, upsert_clause

    document_id = create_document(filename="test2.pdf", status="ready_for_review")
    clause_id, _ = upsert_clause(document_id=document_id, clause_type="termination", extracted_text="Standard termination clause.")
    risk_flag_id = create_risk_flag(clause_id=clause_id, severity="low", assigned_role="reviewer")

    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": risk_flag_id})
    assert resp.status_code == 200, resp.text


def test_review_action_on_unknown_risk_flag_returns_404(app_client):
    _login(app_client, "junior1")
    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": "not-a-real-id"})
    assert resp.status_code == 404


def test_review_action_without_session_is_rejected(app_client):
    resp = app_client.post("/api/review-actions", json={"action": "accept", "risk_flag_id": "anything"})
    assert resp.status_code == 401


def test_public_registration_route_does_not_exist(app_client):
    for path in ("/api/auth/register", "/api/auth/signup", "/api/register"):
        resp = app_client.post(path, json={"username": "new", "password": "new"})
        assert resp.status_code == 404, f"{path} should not exist, got {resp.status_code}"
