"""Section 20-23: confidentiality classification metrics (macro F1), scored
against tests/eval/labeled_confidentiality_eval.json end-to-end (deterministic
signals + live Gemini call + safe combination), not RAGAS (blueprint says not
to use RAGAS as the primary evaluator for confidentiality classification)."""

from __future__ import annotations

import json
import os
from collections import defaultdict

from .confidentiality import LEVELS, classify_document_confidentiality

EVAL_SET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "tests", "eval", "labeled_confidentiality_eval.json",
)


def _macro_f1(confusion: dict[str, dict[str, int]]) -> tuple[float, dict[str, dict]]:
    """confusion[actual][predicted] = count. Returns (macro_f1, per_level_metrics)."""
    per_level = {}
    f1_scores = []
    for level in LEVELS:
        tp = confusion.get(level, {}).get(level, 0)
        fp = sum(confusion.get(a, {}).get(level, 0) for a in LEVELS if a != level)
        fn = sum(confusion.get(level, {}).get(p, 0) for p in LEVELS if p != level)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_level[level] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
                             "support": tp + fn}
        if tp + fn > 0:  # only average over levels actually present in the eval set
            f1_scores.append(f1)
    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    return round(macro_f1, 3), per_level


def run_confidentiality_eval(eval_set_path: str = EVAL_SET_PATH) -> dict:
    """Runs classify_document_confidentiality() against every labeled case and
    returns {"macro_f1", "per_level", "case_results", "dataset_version"}."""
    with open(eval_set_path, encoding="utf-8") as f:
        eval_set = json.load(f)

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    case_results = []
    for case in eval_set["cases"]:
        result = classify_document_confidentiality(case["text"], document_type=case.get("document_type"))
        predicted = result["level"]
        expected = case["expected_level"]
        confusion[expected][predicted] += 1
        case_results.append({
            "domain": "confidentiality", "case_label": case["case_id"], "passed": predicted == expected,
            "scores": {"correct": 1.0 if predicted == expected else 0.0},
            "detail": {"expected": expected, "predicted": predicted, "confidence": result.get("confidence"),
                       "needs_confirmation": result.get("needs_confirmation")},
        })

    macro_f1, per_level = _macro_f1(confusion)
    return {
        "macro_f1": macro_f1, "per_level": per_level, "case_results": case_results,
        "dataset_version": eval_set.get("version", "unknown"), "case_count": len(eval_set["cases"]),
    }
