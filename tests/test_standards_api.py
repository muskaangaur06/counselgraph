"""Phase 5: GET /api/documents/{id}/standards endpoint, and that existing RAG
deviation-scoring/playbook call sites still work when standards_context is
omitted (Phase 4's behavior preserved per the blueprint's exit criteria)."""

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
    monkeypatch.setenv("REVIEWERS", '[{"username":"admin1","password":"pw","role":"admin"}]')

    from legal_graphrag.db import session as session_module
    session_module.reset_engine_for_tests()

    import legal_graphrag.api.main as main_module
    monkeypatch.setattr(main_module, "preload_models", lambda **kwargs: None)

    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        yield c

    session_module.reset_engine_for_tests()
    os.remove(path)


def _login(client, username="admin1", password="pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def test_standards_endpoint_resolves_organization_level(app_client):
    _login(app_client)
    from legal_graphrag.db.repository import create_document
    from legal_graphrag.db.models import KnowledgeReference
    from legal_graphrag.db.session import get_session

    document_id = create_document(filename="test.pdf", status="ready_for_review", document_type="service")

    with get_session() as s:
        s.add(KnowledgeReference(clause_type="liability", reference_text="org standard text",
                                  source_kind="approved_clause", approval_status="approved"))

    resp = app_client.get(f"/api/documents/{document_id}/standards", params={"clause_type": "liability"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope_level"] == "customer_group_fallback"
    assert body["selected"][0]["reference_text"] == "org standard text"


def test_standards_endpoint_404_for_unknown_document(app_client):
    _login(app_client)
    resp = app_client.get("/api/documents/not-a-real-id/standards", params={"clause_type": "liability"})
    assert resp.status_code == 404


def test_standards_endpoint_requires_session(app_client):
    resp = app_client.get("/api/documents/anything/standards", params={"clause_type": "liability"})
    assert resp.status_code == 401


def test_deviation_scoring_works_without_standards_context():
    """Phase 4 behavior preserved: compute_deviation still works when called
    with no standards_context (its default), same as before Phase 5."""
    from legal_graphrag.graphrag.deviation import compute_deviation
    import numpy as np

    class FakeEmbedder:
        def encode(self, text, normalize_embeddings=True):
            return np.array([0.1, 0.2, 0.3])

    result = compute_deviation(
        clause_text="Some clause text", clause_embedding=[0.1, 0.2, 0.3],
        clause_type="nonexistent_clause_type_xyz", org_profile_id=None, embedder=FakeEmbedder(),
    )
    assert result is None  # no approved language on file -- same as pre-Phase-5 behavior
