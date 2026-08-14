"""Section 24.4: citation metrics beyond RAGAS, scored over the same
chat_ragas_log.jsonl records Phase 9 already logs. Deterministic, no LLM call:

- citation recall: material claims (answers with real content) that have at
  least one citation, over all scorable cases.
- citation correctness: every citation string is non-trivial (not empty/
  placeholder) and distinct from the raw answer text (a citation that's just
  a copy of the whole answer isn't pointing at a source).

Citation PRECISION (cited source actually supports the claim) would need an
LLM judge comparing each citation against its contexts -- deliberately left
to the RAGAS runner's faithfulness score instead of a second bespoke judge
call here, to avoid two different LLM calls answering the same underlying
question with possibly different verdicts.
"""

from __future__ import annotations

import json
import os

DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "eval_cache", "chat_ragas_log.jsonl",
)


def _load_cases(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_citation_eval(log_path: str = DEFAULT_LOG_PATH) -> dict:
    cases = [c for c in _load_cases(log_path) if c.get("answer")]
    if not cases:
        return {"citation_recall": None, "citation_correctness": None, "case_results": [], "case_count": 0}

    case_results = []
    recall_hits = []
    correctness_scores = []

    for i, case in enumerate(cases):
        citations = case.get("citations") or []
        has_citation = len(citations) > 0
        recall_hits.append(1.0 if has_citation else 0.0)

        answer_text = (case.get("answer") or "").strip().lower()
        valid_citations = [
            c for c in citations
            if c and len(c.strip()) >= 3 and c.strip().lower() != answer_text
        ]
        correctness = len(valid_citations) / len(citations) if citations else None
        if correctness is not None:
            correctness_scores.append(correctness)

        case_results.append({
            "domain": "citation", "case_label": case.get("question", f"case-{i}"),
            "passed": has_citation and (correctness is None or correctness >= 0.5),
            "scores": {"has_citation": 1.0 if has_citation else 0.0, "correctness": correctness},
            "detail": {"citations": citations},
        })

    return {
        "citation_recall": round(sum(recall_hits) / len(recall_hits), 3),
        "citation_correctness": round(sum(correctness_scores) / len(correctness_scores), 3) if correctness_scores else None,
        "case_results": case_results,
        "case_count": len(cases),
    }
