"""Section 26: OCR and parsing evaluation. Scored against the existing
poor_quality_scanned_nda.pdf sample (already used by tests/eval/run_eval.py's
scanned_no_text_layer anomaly case) -- no full character/word-level ground-truth
transcript exists for it, so Character/Word Error Rate are left unimplemented
(computing them without a real transcript would be a fabricated metric, which
Phase 10's exit criteria explicitly forbids). What IS honestly measurable
without new labeling: page success rate (did OCR recover usable text on a
page that had none) and low-text-page detection recall (did the pipeline
correctly identify which pages needed OCR at all)."""

from __future__ import annotations

import os

from .pdf_pipeline import detect_low_text_pages, extract_page_content, ocr_pages

SAMPLE_CONTRACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "sample_contracts",
)

# hand-labeled: this sample is a fully scanned NDA with no text layer at all,
# so every page is expected to be flagged low-text.
_LABELED_CASES = [
    {"filename": "poor_quality_scanned_nda.pdf", "expect_all_pages_low_text": True},
]


def run_ocr_eval() -> dict:
    case_results = []
    page_success_scores = []
    recall_scores = []

    for case in _LABELED_CASES:
        pdf_path = os.path.join(SAMPLE_CONTRACTS_DIR, case["filename"])
        if not os.path.exists(pdf_path):
            case_results.append({
                "domain": "ocr", "case_label": case["filename"], "passed": None,
                "scores": {}, "detail": {"error": "sample file not found"},
            })
            continue

        pages, _ = extract_page_content(pdf_path)
        low_text_pages = detect_low_text_pages(pages)
        expected_low_text = set(range(1, len(pages) + 1)) if case["expect_all_pages_low_text"] else set()
        detection_recall = (
            len(set(low_text_pages) & expected_low_text) / len(expected_low_text)
            if expected_low_text else 1.0
        )
        recall_scores.append(detection_recall)

        try:
            ocr_results = ocr_pages(pdf_path, low_text_pages)
            pages_recovered = sum(1 for text in ocr_results.values() if text and len(text.strip()) > 20)
            page_success_rate = pages_recovered / len(low_text_pages) if low_text_pages else 1.0
            ocr_error = None
        except Exception as e:  # noqa: BLE001
            page_success_rate = 0.0
            ocr_error = f"{type(e).__name__}: {e}"
        page_success_scores.append(page_success_rate)

        case_results.append({
            "domain": "ocr", "case_label": case["filename"],
            "passed": detection_recall >= 0.9 and page_success_rate >= 0.5,
            "scores": {"low_text_page_detection_recall": round(detection_recall, 3),
                       "page_success_rate": round(page_success_rate, 3)},
            "detail": {"low_text_pages_found": low_text_pages, "total_pages": len(pages), "ocr_error": ocr_error},
        })

    return {
        "avg_page_success_rate": round(sum(page_success_scores) / len(page_success_scores), 3) if page_success_scores else None,
        "avg_low_text_detection_recall": round(sum(recall_scores) / len(recall_scores), 3) if recall_scores else None,
        "case_results": case_results,
        "case_count": len(_LABELED_CASES),
        "known_gap": "Character Error Rate / Word Error Rate not computed: no hand-transcribed ground-truth "
                      "text exists for any sample document yet. Would need a real transcript to avoid a fabricated metric.",
    }
