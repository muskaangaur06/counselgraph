"""Regression test for a real gap found while investigating why the
Operations Dashboard's Citation Recall metric always showed 0.0/FAIL: the
chat pipeline correctly persists citations to chat_message, but
log_ragas_case() (the log citation_metrics.run_citation_eval() scores) never
received or stored them. So citation_metrics always saw an empty list and
scored citation_recall as 0 regardless of whether the answer actually cited
anything. Covers that log_ragas_case now writes the citations field, and that
run_citation_eval reads it correctly once present."""

import json

from counsel_graph.agents.ragas_logging import log_ragas_case
from counsel_graph.agents.citation_metrics import run_citation_eval


def test_log_ragas_case_writes_citations_field(tmp_path, monkeypatch):
    log_path = tmp_path / "chat_ragas_log.jsonl"
    monkeypatch.setattr("counsel_graph.agents.ragas_logging.RAGAS_LOG_PATH", log_path)

    log_ragas_case(
        question="What is the liability cap?",
        answer="The liability cap is twelve months of fees.",
        hybrid_hits=[{"text": "3. Limitation of Liability..."}],
        graph_hits=[],
        document_id="doc-1",
        citations=["Section 3, Limitation of Liability"],
    )

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["citations"] == ["Section 3, Limitation of Liability"]


def test_log_ragas_case_defaults_to_empty_list_when_no_citations_given(tmp_path, monkeypatch):
    log_path = tmp_path / "chat_ragas_log.jsonl"
    monkeypatch.setattr("counsel_graph.agents.ragas_logging.RAGAS_LOG_PATH", log_path)

    log_ragas_case(question="q", answer="a", hybrid_hits=[], graph_hits=[])

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["citations"] == []


def test_citation_eval_scores_recall_once_citations_are_logged(tmp_path):
    """Before the fix, this log format (citations present) was impossible to
    produce -- run_citation_eval always saw citations: [] and scored 0.0.
    With a real citation logged, recall should reflect that."""
    log_path = tmp_path / "chat_ragas_log.jsonl"
    cases = [
        {"question": "q1", "answer": "answer one", "citations": ["Section 3"]},
        {"question": "q2", "answer": "answer two", "citations": []},
    ]
    log_path.write_text("\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8")

    result = run_citation_eval(log_path=str(log_path))

    assert result["case_count"] == 2
    assert result["citation_recall"] == 0.5  # one of two cases had a citation
