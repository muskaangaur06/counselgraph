"""Phase 9: conversational document intelligence (blueprint section 17.5).
Exercises the real legal_pipeline.py graph end-to-end with LLM calls and the
Neo4j-backed store mocked out (no GEMINI_API_KEY/Neo4j needed), against a
throwaway SQLite DB for the ChatMessage persistence layer. Covers: per-document
memory across turns, memory not leaking across documents, citations surviving
into the persisted transcript, and abstention (evidence rejected -> no answer,
no memory fed forward)."""

import os
import tempfile
from unittest.mock import patch

import pytest
from langgraph.types import Command


class _FakeGraphStore:
    """Stands in for Neo4jGraphStore: only the methods legal_pipeline.py's nodes
    actually call, all no-ops except run_read_query (never exercised by these
    tests, since none use the graph route)."""
    def create_query_job(self, *a, **k): pass
    def write_audit_record(self, *a, **k): pass
    def create_reviewer_decision(self, *a, **k): pass
    def update_job_status(self, *a, **k): pass
    def store_query_answer(self, *a, **k): pass
    def record_answered_question(self, *a, **k): return "answered-1"
    def run_read_query(self, *a, **k): return []


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    from counsel_graph.db import session as session_module
    session_module.reset_engine_for_tests()
    session_module.init_db()

    monkeypatch.setattr("counsel_graph.agents.legal_pipeline.get_store", lambda: _FakeGraphStore())

    yield session_module

    session_module.reset_engine_for_tests()
    os.remove(path)


def _make_document(**kwargs):
    from counsel_graph.db.repository import create_document
    defaults = dict(filename="test.pdf", status="ready_for_review")
    defaults.update(kwargs)
    return create_document(**defaults)


def _build_graph():
    from counsel_graph.agents.legal_pipeline import build_legal_agent_graph
    return build_legal_agent_graph()


_HYBRID_HIT = [{"id": "chunk-1", "text": "Termination requires 60 days written notice.", "metadata": {}}]
_ANSWER = {
    "answer": "The contract requires 60 days written notice to terminate.",
    "citations": ["Contract, Clause 5.1"],
    "risk_level": "low",
    "has_uncertainty": False,
}
_SUFFICIENT_VERDICT = {"sufficient": True, "reasoning": "evidence is on point", "gaps": [], "contradictions": []}
_INSUFFICIENT_VERDICT = {"sufficient": False, "reasoning": "no relevant clause found", "gaps": ["termination"], "contradictions": []}


def _ask_and_approve(app, thread_id, question, document_id=None, asked_by="reviewer1"):
    """Runs one full turn: start -> proceed past evidence checkpoint -> approve the draft."""
    config = {"configurable": {"thread_id": thread_id}}
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=_HYBRID_HIT), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_SUFFICIENT_VERDICT), \
         patch("counsel_graph.agents.legal_pipeline.synthesize_legal_answer", return_value=_ANSWER):
        result = app.invoke(
            {"question": question, "collection_name": "test_collection", "document_id": document_id, "asked_by": asked_by},
            config=config,
        )
        assert result["__interrupt__"][0].value["type"] == "evidence_approval_request"

        result = app.invoke(
            Command(resume={"proceed": True, "reviewer": asked_by}), config=config,
        )
        assert result["__interrupt__"][0].value["type"] == "answer_approval_request"

        result = app.invoke(
            Command(resume={"action": "approve", "reviewer": asked_by}), config=config,
        )
    assert result["status"] == "answered"
    return result


def test_conversation_history_loaded_for_second_turn(fresh_db):
    from counsel_graph.agents import legal_pipeline

    document_id = _make_document()
    app = _build_graph()

    _ask_and_approve(app, "thread-1", "What is the termination notice period?", document_id=document_id)

    captured = {}
    orig_start = legal_pipeline.start_job_node

    def _spy_start_job(state):
        result = orig_start(state)
        captured["conversation_history"] = result.get("conversation_history")
        return result

    with patch("counsel_graph.agents.legal_pipeline.start_job_node", side_effect=_spy_start_job):
        # rebuild the graph so the patched node is wired in
        app2 = _build_graph()
        _ask_and_approve(app2, "thread-2", "What about renewal?", document_id=document_id)

    assert captured["conversation_history"] == [
        {"question": "What is the termination notice period?",
         "answer": "The contract requires 60 days written notice to terminate."}
    ]


def test_switching_documents_does_not_leak_context(fresh_db):
    app = _build_graph()
    doc_a = _make_document(filename="a.pdf")
    doc_b = _make_document(filename="b.pdf")

    _ask_and_approve(app, "thread-a1", "What is the termination notice period?", document_id=doc_a)

    from counsel_graph.db.repository import get_chat_history
    history_b_before = get_chat_history(doc_b)
    assert history_b_before == []

    # a fresh turn against doc_b should see NO history at all
    result = None
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=_HYBRID_HIT), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_SUFFICIENT_VERDICT) as mocked_verify, \
         patch("counsel_graph.agents.legal_pipeline.synthesize_legal_answer", return_value=_ANSWER):
        config = {"configurable": {"thread_id": "thread-b1"}}
        app.invoke({"question": "What is the liability cap?", "collection_name": "test_collection",
                    "document_id": doc_b, "asked_by": "reviewer1"}, config=config)

    # verify_evidence's conversation_history kwarg must be empty for doc_b's first turn
    _, kwargs = mocked_verify.call_args
    assert kwargs.get("conversation_history") == []


def test_evidence_rejection_is_abstention_not_hallucination(fresh_db):
    document_id = _make_document()
    app = _build_graph()
    config = {"configurable": {"thread_id": "thread-reject"}}

    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=[]), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_INSUFFICIENT_VERDICT):
        result = app.invoke(
            {"question": "What is the penalty for late delivery?", "collection_name": "test_collection",
             "document_id": document_id, "asked_by": "reviewer1"},
            config=config,
        )
        assert result["__interrupt__"][0].value["evidence_verdict"]["sufficient"] is False

        result = app.invoke(
            Command(resume={"proceed": False, "reviewer": "reviewer1", "comments": "no evidence"}), config=config,
        )

    assert result["status"] == "evidence_rejected"
    assert result["final_answer"] is None

    from counsel_graph.db.repository import get_chat_history
    history = get_chat_history(document_id)
    assert len(history) == 1
    assert history[0]["status"] == "evidence_rejected"
    assert history[0]["answer"] is None

    # a rejected turn must never be fed back in as established conversational memory
    app2 = _build_graph()
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=_HYBRID_HIT), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_SUFFICIENT_VERDICT) as mocked_verify, \
         patch("counsel_graph.agents.legal_pipeline.synthesize_legal_answer", return_value=_ANSWER):
        config2 = {"configurable": {"thread_id": "thread-followup"}}
        app2.invoke({"question": "What about the delivery terms?", "collection_name": "test_collection",
                     "document_id": document_id, "asked_by": "reviewer1"}, config=config2)
    _, kwargs = mocked_verify.call_args
    assert kwargs.get("conversation_history") == []


def test_citations_persist_in_chat_transcript(fresh_db):
    document_id = _make_document()
    app = _build_graph()
    _ask_and_approve(app, "thread-cite", "What is the termination notice period?", document_id=document_id)

    from counsel_graph.db.repository import get_chat_history
    history = get_chat_history(document_id)
    assert history[0]["citations"] == ["Contract, Clause 5.1"]
    assert history[0]["status"] == "answered"


def test_clear_chat_stops_feeding_history_but_keeps_transcript(fresh_db):
    document_id = _make_document()
    app = _build_graph()
    _ask_and_approve(app, "thread-clear1", "What is the termination notice period?", document_id=document_id)

    from counsel_graph.db.repository import clear_chat_history, get_chat_history
    clear_chat_history(document_id)

    assert get_chat_history(document_id) == []
    assert len(get_chat_history(document_id, include_cleared=True)) == 1

    app2 = _build_graph()
    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=_HYBRID_HIT), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_SUFFICIENT_VERDICT) as mocked_verify, \
         patch("counsel_graph.agents.legal_pipeline.synthesize_legal_answer", return_value=_ANSWER):
        config = {"configurable": {"thread_id": "thread-clear2"}}
        app2.invoke({"question": "What about renewal?", "collection_name": "test_collection",
                     "document_id": document_id, "asked_by": "reviewer1"}, config=config)
    _, kwargs = mocked_verify.call_args
    assert kwargs.get("conversation_history") == []


def test_no_document_id_skips_memory_without_error(fresh_db):
    """A caller that never selected a document (document_id=None) should still
    work -- no memory to load, no ChatMessage persisted, no crash."""
    app = _build_graph()
    result = _ask_and_approve(app, "thread-nodoc", "What is the termination notice period?", document_id=None)
    assert result["status"] == "answered"


def test_standards_context_included_when_clause_type_detected(fresh_db):
    from counsel_graph.db.repository import create_document

    document_id = create_document(filename="test.pdf", status="ready_for_review", document_type="Service")
    app = _build_graph()
    config = {"configurable": {"thread_id": "thread-standards"}}

    fake_resolved = {"selected": [{"knowledge_reference_id": "kr-1", "title": "Liability Cap Standard"}],
                      "scope_level": "customer_group_fallback", "source": "kr-1"}

    with patch("counsel_graph.agents.legal_pipeline.hybrid_search", return_value=_HYBRID_HIT), \
         patch("counsel_graph.graphrag.standards.resolve_standards", return_value=fake_resolved), \
         patch("counsel_graph.agents.legal_pipeline.verify_evidence", return_value=_SUFFICIENT_VERDICT) as mocked_verify, \
         patch("counsel_graph.agents.legal_pipeline.synthesize_legal_answer", return_value=_ANSWER):
        app.invoke(
            {"question": "What is the liability cap in this agreement?", "collection_name": "test_collection",
             "document_id": document_id, "asked_by": "reviewer1"},
            config=config,
        )

    _, kwargs = mocked_verify.call_args
    assert kwargs.get("standards_context") is not None
    assert kwargs["standards_context"]["clause_type"] == "liability_cap"
