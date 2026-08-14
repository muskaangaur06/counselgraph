"""Phase 10 task 12: unit tests for metric FORMULAS themselves (macro F1,
citation recall/correctness), independent of any live LLM call. Confidentiality/
clause/risk/OCR/retrieval domain runners need live models/LLM calls and are
exercised by scripts/run_evaluation.py directly, not re-tested here."""

from counsel_graph.graphrag.confidentiality_metrics import _macro_f1
from counsel_graph.agents.citation_metrics import run_citation_eval


def test_macro_f1_perfect_predictions():
    confusion = {"public": {"public": 2}, "internal": {"internal": 3}}
    macro_f1, per_level = _macro_f1(confusion)
    assert macro_f1 == 1.0
    assert per_level["public"]["f1"] == 1.0


def test_macro_f1_all_wrong():
    confusion = {"public": {"internal": 2}}
    macro_f1, per_level = _macro_f1(confusion)
    assert macro_f1 == 0.0


def test_macro_f1_mixed():
    # 1 correct, 1 misclassified as internal
    confusion = {"public": {"public": 1, "internal": 1}}
    macro_f1, per_level = _macro_f1(confusion)
    assert per_level["public"]["recall"] == 0.5
    assert per_level["internal"]["precision"] == 0.0  # internal predicted once, but it was actually "public"


def test_citation_eval_empty_log_returns_none(tmp_path):
    empty_log = tmp_path / "empty.jsonl"
    empty_log.write_text("", encoding="utf-8")
    result = run_citation_eval(str(empty_log))
    assert result["citation_recall"] is None
    assert result["case_count"] == 0


def test_citation_eval_scores_recall_and_correctness(tmp_path):
    import json
    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps({"question": "q1", "answer": "60 days notice.", "contexts": ["ctx"],
                    "citations": ["Clause 5.1"]}) + "\n" +
        json.dumps({"question": "q2", "answer": "No cap stated.", "contexts": ["ctx"],
                    "citations": []}) + "\n",
        encoding="utf-8",
    )
    result = run_citation_eval(str(log))
    assert result["case_count"] == 2
    assert result["citation_recall"] == 0.5  # 1 of 2 cases had a citation
    assert result["citation_correctness"] == 1.0  # the one citation present was valid


def test_citation_eval_flags_citation_that_duplicates_answer(tmp_path):
    import json
    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps({"question": "q1", "answer": "60 days notice.", "contexts": ["ctx"],
                    "citations": ["60 days notice."]}) + "\n",
        encoding="utf-8",
    )
    result = run_citation_eval(str(log))
    assert result["citation_correctness"] == 0.0
