"""Phase 7: summary editing and version history (blueprint section 14.1).
Repository-level tests against a throwaway SQLite DB -- no Neo4j/Gemini needed,
add_summary_version/approve_summary_version/restore_summary_version are pure
Postgres-layer functions."""

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


def _make_document(fresh_db):
    from counsel_graph.db.repository import create_document
    return create_document(filename="test.pdf", status="ready_for_review")


def test_initial_ai_generated_version_is_marked_correctly(fresh_db):
    from counsel_graph.db.repository import add_summary_version

    document_id = _make_document(fresh_db)
    v1 = add_summary_version(document_id, "Initial AI-generated summary.", edited_by=None)
    assert v1["version_number"] == 1
    assert v1["is_ai_generated"] is True
    assert v1["approval_status"] == "draft"


def test_human_edit_creates_new_version_not_overwrite(fresh_db):
    from counsel_graph.db.repository import add_summary_version, get_summary_history

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "Version one text.", edited_by=None)
    add_summary_version(document_id, "Version two, edited by a human.", edited_by="reviewer1")

    history = get_summary_history(document_id)
    assert len(history) == 2
    assert history[0]["summary_text"] == "Version one text."
    assert history[1]["summary_text"] == "Version two, edited by a human."
    assert history[1]["is_ai_generated"] is False
    assert history[1]["edited_by"] == "reviewer1"


def test_latest_summary_version_returns_most_recent_not_oldest(fresh_db):
    """Regression test for the pre-existing bug: document detail used to read
    summary_versions[0] under ascending order, which is the OLDEST version."""
    from counsel_graph.db.repository import add_summary_version, get_latest_summary_version

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "Old text.", edited_by=None)
    add_summary_version(document_id, "Newest text after edits.", edited_by="reviewer1")

    latest = get_latest_summary_version(document_id)
    assert latest["summary_text"] == "Newest text after edits."
    assert latest["version_number"] == 2


def test_approving_a_version_does_not_affect_others(fresh_db):
    from counsel_graph.db.repository import add_summary_version, approve_summary_version, get_summary_history

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "v1 text.", edited_by=None)
    add_summary_version(document_id, "v2 text.", edited_by="reviewer1")
    approve_summary_version(document_id, 1, approved_by="senior1")

    history = get_summary_history(document_id)
    v1, v2 = history[0], history[1]
    assert v1["approval_status"] == "approved"
    assert v1["approved_by"] == "senior1"
    assert v1["approved_at"] is not None
    assert v2["approval_status"] == "draft"


def test_editing_after_approval_preserves_approved_version(fresh_db):
    """Section 14.1: do not overwrite approved summary history. An edit after
    approval must create a new draft version, leaving the approved one intact
    and still readable."""
    from counsel_graph.db.repository import add_summary_version, approve_summary_version, get_summary_history

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "Original text.", edited_by=None)
    approve_summary_version(document_id, 1, approved_by="senior1")
    add_summary_version(document_id, "Edited after approval.", edited_by="reviewer1")

    history = get_summary_history(document_id)
    assert len(history) == 2
    approved_version = next(v for v in history if v["version_number"] == 1)
    assert approved_version["approval_status"] == "approved"
    assert approved_version["summary_text"] == "Original text."


def test_restore_creates_new_version_does_not_delete_history(fresh_db):
    """Section 14.1: old versions remain available. Restoring v1 while v2/v3
    exist must not delete v2/v3 -- it creates a v4 with v1's content."""
    from counsel_graph.db.repository import add_summary_version, restore_summary_version, get_summary_history

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "v1 original.", edited_by=None)
    add_summary_version(document_id, "v2 edited.", edited_by="reviewer1")
    add_summary_version(document_id, "v3 edited again.", edited_by="reviewer2")

    restored = restore_summary_version(document_id, 1, restored_by="reviewer3")
    assert restored["version_number"] == 4
    assert restored["summary_text"] == "v1 original."
    assert restored["restored_from_version"] == 1
    assert restored["is_ai_generated"] is False

    history = get_summary_history(document_id)
    assert len(history) == 4
    assert history[0]["summary_text"] == "v1 original."  # untouched
    assert history[1]["summary_text"] == "v2 edited."  # untouched
    assert history[2]["summary_text"] == "v3 edited again."  # untouched


def test_restoring_unknown_version_raises(fresh_db):
    from counsel_graph.db.repository import add_summary_version, restore_summary_version

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "v1.", edited_by=None)
    with pytest.raises(ValueError):
        restore_summary_version(document_id, 99, restored_by="reviewer1")


def test_approving_unknown_version_raises(fresh_db):
    from counsel_graph.db.repository import add_summary_version, approve_summary_version

    document_id = _make_document(fresh_db)
    add_summary_version(document_id, "v1.", edited_by=None)
    with pytest.raises(ValueError):
        approve_summary_version(document_id, 99, approved_by="senior1")


def test_document_detail_endpoint_shows_latest_not_oldest_summary(fresh_db, monkeypatch):
    """API-level regression test for the executive_summary ordering bug."""
    import counsel_graph.api.main as main_module
    from fastapi.testclient import TestClient

    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.setenv("REVIEWERS", '[{"username":"admin1","password":"pw","role":"admin"}]')
    monkeypatch.setattr(main_module, "preload_models", lambda **kwargs: None)

    from counsel_graph.db.repository import create_document, add_summary_version

    document_id = create_document(filename="test.pdf", status="ready_for_review")
    add_summary_version(document_id, "Old summary text.", edited_by=None)
    add_summary_version(document_id, "Newest summary text.", edited_by="reviewer1")

    with TestClient(main_module.app) as client:
        login = client.post("/api/auth/login", json={"username": "admin1", "password": "pw"})
        assert login.status_code == 200, login.text

        resp = client.get(f"/api/documents/{document_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["executive_summary"] == "Newest summary text."
        assert body["latest_summary_version"]["version_number"] == 2
        assert len(body["summary_versions"]) == 2
