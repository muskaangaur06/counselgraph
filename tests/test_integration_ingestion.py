"""
End-to-end integration test: logs in, uploads a real sample PDF through the actual
API, approves it, then reads back the audit trail. Hits the real Neo4j instance
and a real Chroma collection (not mocked), so it needs a working .env with
GEMINI_API_KEY and NEO4J_* set. Skipped automatically if those aren't present.
"""

import os

import pytest
from fastapi.testclient import TestClient

from legal_graphrag.config import load_env

load_env()

pytestmark = pytest.mark.skipif(
    not (os.getenv("GEMINI_API_KEY") and os.getenv("NEO4J_URI")),
    reason="requires a live GEMINI_API_KEY and a reachable Neo4j instance",
)

_SAMPLE_PDF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "sample_contracts", "sample_vendor_agreement.pdf",
)

_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin@321")


@pytest.fixture
def client():
    """A fresh, logged-in TestClient per test. TestClient's cookie jar keeps
    the session cookie across requests automatically once logged in."""
    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        login_response = c.post(
            "/api/auth/login", json={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200, login_response.text
        yield c


def test_full_ingestion_flow_upload_approve_audit(client):
    with open(_SAMPLE_PDF, "rb") as f:
        response = client.post(
            "/api/ingestion/jobs",
            files={"file": ("sample_vendor_agreement.pdf", f, "application/pdf")},
            data={"vendor_name": "Integration Test Vendor"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "paused"
    assert body["checkpoint"] == "ingestion_approval_request"

    payload = body["payload"]
    assert payload["num_clauses"] > 0
    assert "document_context" in payload
    job_id = payload["job_id"]
    thread_id = body["thread_id"]

    resume_response = client.post(
        f"/api/ingestion/jobs/{thread_id}/resume",
        json={"decision": {"action": "approve", "reviewer": "integration-test", "comments": "automated test run"}},
    )
    assert resume_response.status_code == 200, resume_response.text
    resume_body = resume_response.json()
    assert resume_body["state"] == "completed"
    assert resume_body["result"]["status"] == "approved"

    audit_response = client.get(f"/api/audit/{job_id}")
    assert audit_response.status_code == 200, audit_response.text
    audit_body = audit_response.json()

    actions = [entry["action"] for entry in audit_body["audit_trail"]]
    assert "job_started" in actions
    assert "clauses_extracted" in actions
    assert "review_decision" in actions

    assert len(audit_body["reviewer_decisions"]) == 1
    assert audit_body["reviewer_decisions"][0]["approved"] is True
    assert audit_body["reviewer_decisions"][0]["reviewer"] == "integration-test"

    stats_response = client.get("/api/dashboard/stats")
    assert stats_response.status_code == 200, stats_response.text
    stats = stats_response.json()
    assert stats["total_contracts"] >= 1


def test_login_rejects_wrong_credentials():
    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        response = c.post("/api/auth/login", json={"username": "wrong", "password": "wrong"})
    assert response.status_code == 401


def test_unauthenticated_request_rejected():
    from legal_graphrag.api.main import app
    with TestClient(app) as c:
        with open(_SAMPLE_PDF, "rb") as f:
            response = c.post(
                "/api/ingestion/jobs",
                files={"file": ("sample_vendor_agreement.pdf", f, "application/pdf")},
            )
    assert response.status_code == 401


def test_audit_lookup_for_unknown_job_returns_404(client):
    response = client.get("/api/audit/does-not-exist")
    assert response.status_code == 404
