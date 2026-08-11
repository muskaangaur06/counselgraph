"""
Scores the clause extraction and risk flagging pipeline against
tests/eval/labeled_eval_set.json.

Runs the real pdfplumber -> chunk -> extract_clauses -> flag_risks -> detect_missing_clauses
pipeline against each sample contract (no Neo4j or Chroma needed, since those
functions only need raw text) and reports clause recall, risk-flag precision,
and missing-clause accuracy against the hand-labeled expected answers.

Also scores the anomaly-type documents added for section 9: OCR recovery on a
scanned/no-text-layer page, clause dedup on a document with a repeated clause,
table extraction on a document with an embedded pricing schedule, and
cross-portfolio conflict detection across a document pair with contradictory
termination notice periods.

Usage:
    python tests/eval/run_eval.py
"""

from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from legal_graphrag.ingestion.pdf_pipeline import (  # noqa: E402
    extract_page_content,
    detect_low_text_pages,
    ocr_pages,
    merge_ocr_results,
    compute_page_sections,
    chunk_pages,
)
from legal_graphrag.graphrag.extraction import (  # noqa: E402
    extract_clauses,
    flag_risks,
    detect_missing_clauses,
)
from legal_graphrag.db.dedup import clause_content_hash  # noqa: E402
from legal_graphrag.graphrag.portfolio_conflicts import find_conflicting_clause_pairs  # noqa: E402

EVAL_SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labeled_eval_set.json")
SAMPLE_CONTRACTS_DIR = os.path.join(_REPO_ROOT, "data", "sample_contracts")


def run_pipeline_on_document(pdf_path: str, run_ocr: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Runs extraction + risk flagging. Returns (clauses, risk_flags, extra_info)
    where extra_info includes table_count and whether OCR was actually run.
    run_ocr=True actually OCRs low-text pages (slow); False (the default, matching
    the original eval script) just extracts text-layer content."""
    pages, raw_tables = extract_page_content(pdf_path)
    low_text_pages = detect_low_text_pages(pages)
    ocr_ran = False

    if low_text_pages:
        if run_ocr:
            try:
                ocr_results = ocr_pages(pdf_path, low_text_pages)
                pages = merge_ocr_results(pages, ocr_results)
                ocr_ran = True
            except Exception as e:  # noqa: BLE001
                print(f"  warning: OCR failed for {os.path.basename(pdf_path)} "
                      f"({type(e).__name__}: {e}). Is poppler installed and on PATH? "
                      f"Falling back to text-layer-only extraction for this doc.")
        else:
            print(f"  note: {os.path.basename(pdf_path)} has {len(low_text_pages)} low-text page(s); "
                  f"OCR skipped for this run (pass run_ocr=True to recover clauses from scanned pages).")

    page_sections = compute_page_sections(pages)
    chunks = chunk_pages(pages, os.path.basename(pdf_path), page_sections)

    table_count = sum(len(tables) for tables in raw_tables.values())

    clauses = []
    for chunk in chunks:
        for raw_clause in extract_clauses(chunk.text):
            clauses.append({"id": str(len(clauses)), **raw_clause})

    risks = flag_risks([{"id": c["id"], "clause_type": c.get("clause_type"), "text": c["text"]} for c in clauses])
    extra_info = {"table_count": table_count, "ocr_ran": ocr_ran, "low_text_pages": low_text_pages}
    return clauses, risks, extra_info


def score_document(doc_label: dict, clauses: list[dict], risks: list[dict], extra_info: dict) -> dict:
    anomaly_type = doc_label.get("anomaly_type")
    run_ocr = bool(doc_label.get("requires_ocr"))

    found_clause_types = {c.get("clause_type") for c in clauses if c.get("clause_type")}
    expected_clause_types = set(doc_label["expected_clause_types"])

    true_positives = found_clause_types & expected_clause_types
    false_negatives = expected_clause_types - found_clause_types
    false_positives = found_clause_types - expected_clause_types

    # a scanned/no-text-layer doc with OCR intentionally skipped is expected to
    # find nothing; that's correct behavior for this code path, not a failure,
    # so recall is reported as N/A instead of a misleading 0%.
    ocr_skipped_as_expected = (anomaly_type == "scanned_no_text_layer" and not run_ocr)
    clause_recall = (
        None if ocr_skipped_as_expected
        else (len(true_positives) / len(expected_clause_types) if expected_clause_types else 1.0)
    )

    high_risk_found = {r["clause_id"] for r in risks if r.get("risk_level") == "high"}
    high_risk_clause_types_found = {
        c.get("clause_type") for c in clauses if c["id"] in high_risk_found
    }
    expected_high_risk = set(doc_label["expected_high_risk_clause_types"])
    risk_true_positives = high_risk_clause_types_found & expected_high_risk
    risk_precision = (
        len(risk_true_positives) / len(high_risk_clause_types_found)
        if high_risk_clause_types_found else (1.0 if not expected_high_risk else 0.0)
    )

    missing = detect_missing_clauses(doc_label["contract_type"], list(found_clause_types))
    missing_types_found = {m["clause_type"] for m in missing}
    expected_missing = set(doc_label["expected_missing_clauses"])
    missing_match = missing_types_found == expected_missing

    result = {
        "filename": doc_label["filename"],
        "anomaly_type": anomaly_type,
        "clause_recall": round(clause_recall, 3) if clause_recall is not None else None,
        "clauses_found": sorted(found_clause_types),
        "clauses_missed": sorted(false_negatives),
        "clauses_unexpected": sorted(false_positives),
        "risk_precision": round(risk_precision, 3),
        "high_risk_found": sorted(high_risk_clause_types_found),
        "high_risk_expected": sorted(expected_high_risk),
        "missing_clause_detection_correct": missing_match,
        "missing_clauses_found": sorted(missing_types_found),
        "missing_clauses_expected": sorted(expected_missing),
    }

    # anomaly-specific scoring
    if anomaly_type == "duplicate_clause_dedup":
        expected_type = doc_label["expected_duplicate_clause_type"]
        matching = [c for c in clauses if c.get("clause_type") == expected_type]
        hashes = [clause_content_hash(c.get("clause_type"), None, c.get("text", "")) for c in matching]
        result["dedup_check"] = {
            "raw_extracted_count": len(matching),
            "unique_content_hashes": len(set(hashes)),
            "note": "raw_extracted_count > unique_content_hashes confirms the LLM extracted the clause "
                    "twice; content_hash-based upsert (db/repository.upsert_clause) collapses these to "
                    "one row on persistence, which is the actual dedup point, not extraction itself.",
        }

    if anomaly_type == "embedded_pricing_table":
        result["table_check"] = {
            "tables_found": extra_info["table_count"],
            "expected": doc_label.get("expected_table_count"),
            "correct": extra_info["table_count"] == doc_label.get("expected_table_count"),
        }

    if anomaly_type == "scanned_no_text_layer":
        result["ocr_check"] = {
            "low_text_pages": extra_info["low_text_pages"],
            "ocr_ran": extra_info["ocr_ran"],
        }

    if anomaly_type == "unusual_governing_law":
        gov_law_clauses = [c for c in clauses if c.get("clause_type") == "governing_law"]
        result["governing_law_check"] = {
            "found_governing_law_clause": bool(gov_law_clauses),
            "text_sample": gov_law_clauses[0]["text"][:150] if gov_law_clauses else None,
        }

    return result


def score_conflict_pairs(all_results: dict[str, dict], eval_set: dict) -> list[dict]:
    """Cross-document check for anomaly_type=cross_portfolio_conflict pairs: re-runs
    extraction on both documents (already done in score_document, reuse via
    all_results' cached clauses) and checks find_conflicting_clause_pairs detects
    the expected conflicting clause_type between them."""
    conflict_docs = [d for d in eval_set["documents"] if d.get("anomaly_type") == "cross_portfolio_conflict"]
    seen_pairs = set()
    scored = []

    for doc_label in conflict_docs:
        pair_filename = doc_label.get("conflict_pair_with")
        pair_key = tuple(sorted([doc_label["filename"], pair_filename]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        clauses_a = all_results[doc_label["filename"]]
        clauses_b = all_results[pair_filename]

        clause_rows = [
            {"contract_id": doc_label["filename"], "contract_name": doc_label["filename"],
             "clause_id": c["id"], "clause_type": c.get("clause_type"), "text": c["text"]}
            for c in clauses_a
        ] + [
            {"contract_id": pair_filename, "contract_name": pair_filename,
             "clause_id": c["id"], "clause_type": c.get("clause_type"), "text": c["text"]}
            for c in clauses_b
        ]
        conflicts = find_conflicting_clause_pairs(clause_rows)
        expected_type = doc_label.get("expected_conflict_clause_type")
        found_expected = any(c["clause_type"] == expected_type for c in conflicts)

        scored.append({
            "pair": pair_key,
            "conflicts_found": len(conflicts),
            "expected_conflict_clause_type": expected_type,
            "found_expected_conflict": found_expected,
        })

    return scored


def main():
    with open(EVAL_SET_PATH, encoding="utf-8") as f:
        eval_set = json.load(f)

    # Pacing between documents: the free-tier Gemini API key this pipeline uses
    # is rate-limited to 15 requests/minute, and each document issues several
    # calls (one extract_clauses call per chunk, plus one flag_risks call). A
    # short sleep between documents keeps a full eval run under that limit
    # instead of tripping a 429 partway through.
    import time as _time
    EVAL_PACING_SECONDS = float(os.getenv("EVAL_PACING_SECONDS", "20"))

    results = []
    clauses_by_filename: dict[str, list[dict]] = {}
    for i, doc_label in enumerate(eval_set["documents"]):
        print(f"Scoring {doc_label['filename']}...")
        if i > 0:
            _time.sleep(EVAL_PACING_SECONDS)
        pdf_path = os.path.join(SAMPLE_CONTRACTS_DIR, doc_label["filename"])
        clauses, risks, extra_info = run_pipeline_on_document(pdf_path, run_ocr=bool(doc_label.get("requires_ocr")))
        clauses_by_filename[doc_label["filename"]] = clauses
        results.append(score_document(doc_label, clauses, risks, extra_info))

    print("\n" + "=" * 70)
    print("EVAL RESULTS")
    print("=" * 70)
    for r in results:
        print(f"\n{r['filename']}" + (f"  [{r['anomaly_type']}]" if r.get("anomaly_type") else ""))
        recall_str = "N/A (OCR skipped by design)" if r["clause_recall"] is None else f"{r['clause_recall']:.0%}"
        print(f"  clause recall:  {recall_str}  "
              f"(found {r['clauses_found']}, missed {r['clauses_missed'] or 'none'})")
        print(f"  risk precision: {r['risk_precision']:.0%}  "
              f"(flagged high-risk: {r['high_risk_found']}, expected: {r['high_risk_expected']})")
        print(f"  missing-clause detection: {'correct' if r['missing_clause_detection_correct'] else 'MISMATCH'}  "
              f"(found {r['missing_clauses_found']}, expected {r['missing_clauses_expected']})")
        if "dedup_check" in r:
            print(f"  dedup check: {r['dedup_check']}")
        if "table_check" in r:
            print(f"  table check: {r['table_check']}")
        if "ocr_check" in r:
            print(f"  ocr check: {r['ocr_check']}")
        if "governing_law_check" in r:
            print(f"  governing law check: {r['governing_law_check']}")

    print("\n" + "-" * 70)
    print("CROSS-PORTFOLIO CONFLICT DETECTION")
    print("-" * 70)
    conflict_results = score_conflict_pairs(clauses_by_filename, eval_set)
    for cr in conflict_results:
        status = "correct" if cr["found_expected_conflict"] else "MISMATCH"
        print(f"  {cr['pair']}: {cr['conflicts_found']} conflict(s) found, "
              f"expected type={cr['expected_conflict_clause_type']} -> {status}")

    scored_recalls = [r["clause_recall"] for r in results if r["clause_recall"] is not None]
    avg_recall = sum(scored_recalls) / len(scored_recalls) if scored_recalls else 0.0
    avg_precision = sum(r["risk_precision"] for r in results) / len(results)
    print(f"\nAverage clause recall (excluding OCR-skipped docs): {avg_recall:.0%}")
    print(f"Average risk precision: {avg_precision:.0%}")

    return results


if __name__ == "__main__":
    main()
