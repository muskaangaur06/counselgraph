"""Phase 10: CLI entrypoint for the AI evaluation framework (section 20-28's
persistence/CLI requirement -- "Add CLI/API for running evaluations").

Runs every metric domain that has a labeled dataset, persists one
EvaluationRun row (plus per-case EvaluationCaseResult rows) via
db.repository.create_evaluation_run(), and prints a summary.

RAGAS metrics run in a SEPARATE subprocess using .venv-ragas's interpreter,
not this process's -- see scripts/run_ragas_eval.py's docstring for why
(current ragas releases are incompatible with this app's own langgraph/
langchain-core pins). Requires the main app's normal .venv to run this script
itself, and requires GEMINI_API_KEY for the LLM-backed domains (confidentiality,
clause/risk extraction, RAGAS) -- domains that need it are skipped, not
fabricated, if it's unset.

Usage:
    .venv/Scripts/python.exe scripts/run_evaluation.py [--skip-ragas] [--skip-retrieval] [--triggered-by NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "tests", "eval"))

RAGAS_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv-ragas", "Scripts", "python.exe")
RAGAS_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "run_ragas_eval.py")


def _git_commit_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _run_clause_risk_eval() -> dict | None:
    if not os.getenv("GEMINI_API_KEY"):
        print("  skipped (GEMINI_API_KEY not set)")
        return None
    from run_eval import main as run_eval_main
    results = run_eval_main()
    scored_recalls = [r["clause_recall"] for r in results if r["clause_recall"] is not None]
    avg_recall = sum(scored_recalls) / len(scored_recalls) if scored_recalls else 0.0
    avg_precision = sum(r["risk_precision"] for r in results) / len(results) if results else 0.0
    case_results = [
        {"domain": "clause", "case_label": r["filename"], "passed": r["clause_recall"] is None or r["clause_recall"] >= 0.5,
         "scores": {"clause_recall": r["clause_recall"]}, "detail": {"missed": r["clauses_missed"]}}
        for r in results
    ] + [
        {"domain": "risk", "case_label": r["filename"], "passed": r["risk_precision"] >= 0.0,
         "scores": {"risk_precision": r["risk_precision"]}, "detail": {"high_risk_found": r["high_risk_found"]}}
        for r in results
    ]
    return {
        "metrics": {"avg_clause_recall": round(avg_recall, 3), "avg_risk_precision": round(avg_precision, 3)},
        "case_results": case_results, "case_count": len(results),
    }


def _run_confidentiality_eval() -> dict | None:
    if not os.getenv("GEMINI_API_KEY"):
        print("  skipped (GEMINI_API_KEY not set)")
        return None
    from counsel_graph.graphrag.confidentiality_metrics import run_confidentiality_eval
    result = run_confidentiality_eval()
    return {
        "metrics": {"macro_f1": result["macro_f1"], "per_level": result["per_level"],
                    "dataset_version": result["dataset_version"]},
        "case_results": result["case_results"], "case_count": result["case_count"],
    }


def _run_ocr_eval() -> dict:
    from counsel_graph.ingestion.ocr_metrics import run_ocr_eval
    result = run_ocr_eval()
    return {
        "metrics": {"avg_page_success_rate": result["avg_page_success_rate"],
                    "avg_low_text_detection_recall": result["avg_low_text_detection_recall"],
                    "known_gap": result["known_gap"]},
        "case_results": result["case_results"], "case_count": result["case_count"],
    }


def _run_retrieval_eval() -> dict | None:
    from counsel_graph.retrieval.retrieval_metrics import run_retrieval_eval
    result = run_retrieval_eval()
    if result.get("status") == "skipped":
        print(f"  skipped ({result['reason']})")
        return None
    return {
        "metrics": {"recall_at_k": result["recall_at_k"], "precision_at_k": result["precision_at_k"],
                    "mrr": result["mrr"], "wrong_tenant_retrieval_count": result["wrong_tenant_retrieval_count"],
                    "wrong_tenant_retrieval_rate": result["wrong_tenant_retrieval_rate"]},
        "case_results": result["case_results"], "case_count": result["case_count"],
    }


def _run_citation_eval() -> dict:
    from counsel_graph.agents.citation_metrics import run_citation_eval
    result = run_citation_eval()
    return {
        "metrics": {"citation_recall": result["citation_recall"], "citation_correctness": result["citation_correctness"]},
        "case_results": result["case_results"], "case_count": result["case_count"],
    }


def _run_ragas_eval() -> dict | None:
    if not os.path.exists(RAGAS_VENV_PYTHON):
        print(f"  skipped (.venv-ragas not found at {RAGAS_VENV_PYTHON} -- see requirements-ragas.txt to set it up)")
        return None
    if not os.getenv("GEMINI_API_KEY"):
        print("  skipped (GEMINI_API_KEY not set)")
        return None
    proc = subprocess.run(
        [RAGAS_VENV_PYTHON, RAGAS_SCRIPT], capture_output=True, text=True, timeout=600,
        env={**os.environ},
    )
    if proc.returncode != 0:
        print(f"  RAGAS subprocess failed: {proc.stderr[-1000:]}")
        return None
    try:
        # subprocess stdout may include tqdm progress lines before the final JSON line
        last_line = [l for l in proc.stdout.splitlines() if l.strip()][-1]
        result = json.loads(last_line)
    except (IndexError, json.JSONDecodeError) as e:
        print(f"  RAGAS subprocess output not parseable: {type(e).__name__}: {e}")
        return None
    if "error" in result:
        print(f"  RAGAS: {result['error']}")
        return None
    case_results = [
        {"domain": "ragas", "case_label": c["question"], "passed": None,
         "scores": {k: c[k] for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall")},
         "detail": {"document_id": c.get("document_id"), "reference_source": c.get("reference_source")}}
        for c in result.get("cases", [])
    ]
    return {"metrics": result.get("aggregate", {}), "case_results": case_results,
            "case_count": result.get("aggregate", {}).get("case_count", 0)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ragas", action="store_true")
    parser.add_argument("--skip-retrieval", action="store_true")
    parser.add_argument("--skip-clause-risk", action="store_true")
    parser.add_argument("--skip-confidentiality", action="store_true")
    parser.add_argument("--triggered-by", default="cli")
    args = parser.parse_args()

    from counsel_graph.db.repository import create_evaluation_run
    from counsel_graph.db.session import init_db

    init_db()

    started = time.time()
    metrics: dict = {}
    case_counts: dict = {}
    all_case_results: list[dict] = []
    dataset_versions: dict = {}

    domains = [
        ("clause_risk", _run_clause_risk_eval, args.skip_clause_risk),
        ("confidentiality", _run_confidentiality_eval, args.skip_confidentiality),
        ("ocr", _run_ocr_eval, False),
        ("retrieval", _run_retrieval_eval, args.skip_retrieval),
        ("citation", _run_citation_eval, False),
        ("ragas", _run_ragas_eval, args.skip_ragas),
    ]

    for domain_name, fn, skip in domains:
        print(f"Running {domain_name} eval...")
        if skip:
            print("  skipped (--skip flag)")
            continue
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {type(e).__name__}: {e}")
            continue
        if result is None:
            continue
        metrics[domain_name] = result["metrics"]
        case_counts[domain_name] = result["case_count"]
        all_case_results.extend(result["case_results"])
        if "dataset_version" in result.get("metrics", {}):
            dataset_versions[domain_name] = result["metrics"]["dataset_version"]

    duration = round(time.time() - started, 2)

    run = create_evaluation_run(
        triggered_by=args.triggered_by, commit_sha=_git_commit_sha(), gemini_model="gemini-flash-lite-latest",
        embedding_model="all-MiniLM-L6-v2 (retrieval) / gemini-embedding-001 (RAGAS judge)",
        dataset_versions=dataset_versions, metrics=metrics, case_counts=case_counts,
        duration_seconds=duration, case_results=all_case_results,
        status="completed" if metrics else "failed",
        error_detail=None if metrics else "no domain produced any metrics -- check GEMINI_API_KEY / .venv-ragas setup",
    )

    print("\n" + "=" * 70)
    print(f"EVALUATION RUN {run['evaluation_run_id']} ({duration}s)")
    print("=" * 70)
    print(json.dumps(metrics, indent=2, default=str))
    print(f"\nCase counts: {case_counts}")

    return 0 if metrics else 1


if __name__ == "__main__":
    sys.exit(main())
