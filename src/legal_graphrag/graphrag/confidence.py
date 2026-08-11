"""Section 7: Evidence-Weighted Confidence.

Combines (a) OCR page-confidence for the clause's source page, (b) retrieval-
relevance of any cited knowledge_reference, and (c) the LLM's self-reported
confidence into one composite score, keeping the three components in a stored
breakdown (not just the final number) so the UI can show e.g. "low confidence:
source OCR quality 61%".
"""

from __future__ import annotations

from typing import Optional

# equal-weighted by default; adjust here if one signal proves more reliable in practice
_WEIGHTS = {"ocr_quality": 0.3, "retrieval_relevance": 0.3, "llm_confidence": 0.4}


def compute_evidence_weighted_confidence(
    ocr_quality: Optional[float],
    retrieval_relevance: Optional[float],
    llm_confidence: Optional[float],
) -> dict:
    """Each component is 0.0-1.0, or None if not applicable (e.g. no OCR was run on
    a text-layer page, or no knowledge_reference was cited). Missing components are
    excluded from the weighted average and their weight is redistributed across
    the remaining components, rather than silently treating "unknown" as "bad"."""
    components = {
        "ocr_quality": ocr_quality,
        "retrieval_relevance": retrieval_relevance,
        "llm_confidence": llm_confidence,
    }
    present = {k: v for k, v in components.items() if v is not None}

    if not present:
        composite = None
    else:
        total_weight = sum(_WEIGHTS[k] for k in present)
        composite = round(sum(_WEIGHTS[k] * v for k, v in present.items()) / total_weight, 3)

    return {
        "composite": composite,
        "components": {
            "ocr_quality": ocr_quality,
            "retrieval_relevance": retrieval_relevance,
            "llm_confidence": llm_confidence,
        },
    }


def describe_low_confidence(breakdown: dict, threshold: float = 0.7) -> Optional[str]:
    """Human-readable reason when composite confidence is low, naming the weakest
    component (e.g. "low confidence: source OCR quality 61%") rather than just
    showing a bare number."""
    composite = breakdown.get("composite")
    if composite is None or composite >= threshold:
        return None
    components = breakdown["components"]
    labels = {"ocr_quality": "source OCR quality", "retrieval_relevance": "retrieval relevance",
              "llm_confidence": "model self-reported confidence"}
    present = {k: v for k, v in components.items() if v is not None}
    if not present:
        return f"low confidence: {composite:.0%}"
    weakest_key = min(present, key=lambda k: present[k])
    return f"low confidence: {labels[weakest_key]} {present[weakest_key]:.0%}"
