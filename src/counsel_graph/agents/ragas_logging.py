"""Section 17.5/24.2: RAGAS-compatible logging for the document chat pipeline.

This module only produces the evaluation CASE FORMAT section 24.2 specifies
(question, generated answer, retrieved contexts, document/organization/
jurisdiction/expected-standard-source metadata) as append-only JSONL records.
It does not run RAGAS itself -- no ragas package dependency is added here,
consistent with how the existing eval harness (tests/eval/run_eval.py) also
only runs against a labeled set on demand, not on every request. A future
phase that actually computes RAGAS metrics (faithfulness, answer relevancy,
context precision/recall per section 24.1) would read this same log file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAGAS_LOG_PATH = _PROJECT_ROOT / "data" / "eval_cache" / "chat_ragas_log.jsonl"


def _extract_contexts(hybrid_hits: list[dict], graph_hits: list[dict]) -> list[str]:
    """Flattens retrieved evidence into plain-text contexts, the shape RAGAS
    metrics (faithfulness, context precision/recall) expect -- a list of
    strings, not the richer structured hit dicts this app uses internally."""
    contexts: list[str] = []
    for hit in hybrid_hits or []:
        text = hit.get("text") if isinstance(hit, dict) else None
        if text:
            contexts.append(text)
    for hit in graph_hits or []:
        if isinstance(hit, dict):
            text = hit.get("clause_text") or hit.get("text") or json.dumps(hit, default=str)
            contexts.append(text)
    return contexts


def log_ragas_case(question: str, answer: Optional[str], hybrid_hits: list[dict], graph_hits: list[dict],
                    document_id: Optional[str] = None, organization: Optional[str] = None,
                    jurisdiction: Optional[str] = None, expected_standard_source: Optional[str] = None,
                    reference_answer: Optional[str] = None, reference_contexts: Optional[list[str]] = None,
                    citations: Optional[list[str]] = None) -> None:
    """Appends one section-24.2-shaped record. Never raises -- logging failures
    must not break the chat turn itself, same reasoning as every other
    best-effort side write in this pipeline (audit records, graph updates).

    citations is the same list already persisted to chat_message by the caller
    -- record_chat_message() gets it, this log did not, which meant
    citation_metrics.run_citation_eval() always saw an empty list and scored
    citation_recall as 0.0 regardless of whether the answer actually cited
    anything."""
    record = {
        "question": question,
        "answer": answer,
        "contexts": _extract_contexts(hybrid_hits, graph_hits),
        "citations": citations or [],
        "reference_answer": reference_answer,
        "reference_contexts": reference_contexts or [],
        "document_id": document_id,
        "organization": organization,
        "jurisdiction": jurisdiction,
        "expected_standard_source": expected_standard_source,
    }
    try:
        RAGAS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RAGAS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + os.linesep)
    except OSError as e:
        print(f"[ragas_logging] WARNING: failed to write eval log: {type(e).__name__}: {e}")
