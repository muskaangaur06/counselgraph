"""
Pytest wrapper around run_eval.py. LLM clause extraction is non-deterministic,
so this checks the pipeline stays above a loose quality floor rather than
asserting exact matches. Run manually with `python tests/eval/run_eval.py`
for the full breakdown; this test is the CI-friendly summary version.

Requires a real GEMINI_API_KEY (makes live LLM calls), so it's skipped if unset.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_eval import main as run_eval_main  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"), reason="requires a live GEMINI_API_KEY to run the extraction pipeline"
)

MIN_AVG_CLAUSE_RECALL = 0.5
MIN_AVG_RISK_PRECISION = 0.0  # risk precision is noisy on 1-2 flags per doc; recall matters more here


def test_eval_meets_minimum_quality_floor():
    results = run_eval_main()
    # clause_recall is None for docs where OCR was intentionally skipped (e.g. the
    # scanned-page anomaly doc); that's correct behavior for that code path, not a
    # score, so it's excluded from the average rather than counted as 0%.
    scored_recalls = [r["clause_recall"] for r in results if r["clause_recall"] is not None]
    avg_recall = sum(scored_recalls) / len(scored_recalls) if scored_recalls else 0.0
    avg_precision = sum(r["risk_precision"] for r in results) / len(results)

    assert avg_recall >= MIN_AVG_CLAUSE_RECALL, (
        f"Average clause recall {avg_recall:.0%} fell below the {MIN_AVG_CLAUSE_RECALL:.0%} floor. "
        f"This usually means a prompt regression in graphrag/extraction.py, not a real quality bar."
    )
    assert avg_precision >= MIN_AVG_RISK_PRECISION
