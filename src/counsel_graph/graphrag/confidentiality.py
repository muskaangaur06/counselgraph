"""Automatic confidentiality classification (blueprint section 12): a hybrid of
deterministic keyword/label signals and a Gemini structured call, combined by
rules that never let the LLM silently downgrade an explicit label and always
fall back to the safer level on disagreement or low confidence.
"""

from __future__ import annotations

import re
from typing import Optional

from ..llm_client import call_json

LEVELS = ["public", "internal", "confidential", "highly_confidential"]
_LEVEL_RANK = {level: i for i, level in enumerate(LEVELS)}

# explicit document labels: if present, the level they imply cannot be
# downgraded by the LLM (section 12.2.C)
_EXPLICIT_LABEL_PATTERNS = [
    (re.compile(r"\bstrictly\s+confidential\b", re.IGNORECASE), "highly_confidential"),
    (re.compile(r"\bconfidential\b", re.IGNORECASE), "confidential"),
    (re.compile(r"\binternal\s+use\s+only\b", re.IGNORECASE), "internal"),
    (re.compile(r"\bpublic\s+filing\b", re.IGNORECASE), "public"),
]

# sensitive-content signals: each hit nudges the deterministic level up, but
# never past highly_confidential and never used alone to justify "public"
_SENSITIVE_SIGNALS = {
    "m&a": ["merger", "acquisition", "m&a", "amalgamation"],
    "valuation": ["valuation", "fair market value", "enterprise value"],
    "trade_secrets": ["trade secret", "proprietary technology", "proprietary process"],
    "board_materials": ["board materials", "board resolution", "board of directors meeting"],
    "tender_bid": ["tender", "bid submission", "request for proposal"],
    "pricing": ["unit price", "pricing schedule", "detailed pricing"],
    "personal_data": ["personal data", "personally identifiable information", "date of birth", "passport number"],
    "salary": ["salary", "compensation package", "remuneration details"],
    "financial_statements": ["balance sheet", "income statement", "financial statements", "profit and loss"],
    "nda": ["non-disclosure agreement", "nondisclosure agreement"],
    "data_processing": ["data processing agreement", "data processing terms", "processing of personal data"],
}

_SIGNAL_LEVEL = {
    "m&a": "highly_confidential",
    "valuation": "highly_confidential",
    "trade_secrets": "highly_confidential",
    "board_materials": "highly_confidential",
    "tender_bid": "confidential",
    "pricing": "confidential",
    "personal_data": "confidential",
    "salary": "confidential",
    "financial_statements": "confidential",
    "nda": "confidential",
    "data_processing": "confidential",
}


def detect_deterministic_signals(text: str, document_type: Optional[str] = None) -> dict:
    """Scans text for explicit labels and sensitive-content keywords. Returns
    {"explicit_level", "explicit_evidence", "signals": [...], "suggested_level"}.
    explicit_level is None unless an explicit label was found; suggested_level
    is the deterministic best guess (explicit label if present, else the
    highest-ranked sensitive signal, else the document-type default, else None)."""
    explicit_level = None
    explicit_evidence = None
    for pattern, level in _EXPLICIT_LABEL_PATTERNS:
        match = pattern.search(text)
        if match:
            explicit_level = level
            explicit_evidence = match.group(0)
            break  # patterns are ordered most-specific first

    signals_found = []
    for signal_name, keywords in _SENSITIVE_SIGNALS.items():
        for kw in keywords:
            if re.search(re.escape(kw), text, re.IGNORECASE):
                signals_found.append(signal_name)
                break

    suggested_level = explicit_level
    if suggested_level is None and signals_found:
        suggested_level = max((_SIGNAL_LEVEL[s] for s in signals_found), key=_LEVEL_RANK.get)
    if suggested_level is None and document_type:
        # a conservative, non-authoritative document-type prior; never used to
        # justify "public" on its own (section 12.2.C: never silently mark public
        # solely because no keywords were found)
        if document_type.strip().lower() in ("nda", "non-disclosure"):
            suggested_level = "confidential"

    return {
        "explicit_level": explicit_level,
        "explicit_evidence": explicit_evidence,
        "signals": signals_found,
        "suggested_level": suggested_level,
    }


_CLASSIFY_SYSTEM_PROMPT = f"""You classify the confidentiality level of a legal/contract \
document for an internal legal review system.

Allowed levels (least to most sensitive): {", ".join(LEVELS)}.

You are given the first relevant pages of the document, its metadata, and a list of \
deterministic signals a keyword scan already found. Weigh all of this and return your \
own independent judgment as JSON matching exactly this schema:

{{
  "level": "one of: {' | '.join(LEVELS)}",
  "confidence": 0.0 to 1.0,
  "reasons": [
    {{"reason": "short explanation", "page": 1, "evidence": "exact short evidence excerpt"}}
  ],
  "sensitive_data_detected": ["personal_data", "financial_terms", ...]
}}

Output ONLY the JSON object, no markdown fences, no prose.
"""


def classify_with_llm(text_excerpt: str, document_metadata: dict, deterministic_signals: dict) -> dict:
    """Calls Gemini for a structured confidentiality judgment. Returns a dict with
    level/confidence/reasons/sensitive_data_detected, or a safe low-confidence
    'internal' fallback if the call fails (never silently defaults to public)."""
    import json

    user_prompt = json.dumps({
        "document_excerpt": text_excerpt[:8000],
        "document_metadata": document_metadata,
        "deterministic_signals": deterministic_signals,
        "allowed_levels": LEVELS,
    })
    try:
        result = call_json(_CLASSIFY_SYSTEM_PROMPT, user_prompt)
    except Exception as e:  # noqa: BLE001
        print(f"[confidentiality] WARNING: LLM classification failed: {type(e).__name__}: {e}")
        return {"level": "internal", "confidence": 0.0, "reasons": [], "sensitive_data_detected": []}

    if not isinstance(result, dict) or result.get("level") not in LEVELS:
        return {"level": "internal", "confidence": 0.0, "reasons": [], "sensitive_data_detected": []}

    result.setdefault("confidence", 0.0)
    result.setdefault("reasons", [])
    result.setdefault("sensitive_data_detected", [])
    return result


def combine_classification(deterministic: dict, llm_result: dict, ocr_ratio: float = 0.0) -> dict:
    """Safely combines deterministic signals with the LLM's judgment per section
    12.2.C's rules. Returns {"level", "confidence", "source", "reasons",
    "needs_confirmation"}.

    Rules applied:
    - an explicit label can never be downgraded by the LLM;
    - if deterministic and LLM disagree by more than one level, flag for
      human confirmation instead of silently picking one;
    - low OCR confidence (many pages needed OCR) reduces final confidence;
    - ambiguous/disagreeing results default to the safer (higher) level;
    - low-confidence results are marked as such, never silently public.
    """
    explicit_level = deterministic.get("explicit_level")
    det_level = deterministic.get("suggested_level")
    llm_level = llm_result.get("level", "internal")
    llm_confidence = float(llm_result.get("confidence") or 0.0)

    needs_confirmation = False

    if explicit_level is not None:
        # explicit label wins outright unless the LLM found something even more
        # sensitive (never a downgrade, an upgrade is still allowed)
        final_level = max(explicit_level, llm_level, key=_LEVEL_RANK.get)
        confidence = max(llm_confidence, 0.9)
        source_note = "explicit_label"
    elif det_level is not None:
        gap = abs(_LEVEL_RANK[det_level] - _LEVEL_RANK[llm_level])
        if gap > 1:
            needs_confirmation = True
            final_level = max(det_level, llm_level, key=_LEVEL_RANK.get)
            confidence = min(llm_confidence, 0.5)
        else:
            final_level = max(det_level, llm_level, key=_LEVEL_RANK.get)
            confidence = llm_confidence
        source_note = "deterministic_plus_llm"
    else:
        final_level = llm_level
        confidence = llm_confidence
        source_note = "llm_only"

    # low OCR quality erodes trust in whatever text the signals/LLM saw
    if ocr_ratio > 0.5:
        confidence = min(confidence, 0.4)
        needs_confirmation = needs_confirmation or ocr_ratio > 0.8

    # never silently mark public purely on the absence of signals/low confidence
    if final_level == "public" and (confidence < 0.6 or det_level is None):
        final_level = "internal"
        needs_confirmation = True

    reasons = list(llm_result.get("reasons") or [])
    if explicit_level and deterministic.get("explicit_evidence"):
        reasons.insert(0, {
            "reason": f"Explicit label found implying '{explicit_level}'.",
            "page": None,
            "evidence": deterministic["explicit_evidence"],
        })

    return {
        "level": final_level,
        "confidence": round(confidence, 3),
        "source": "automatic",
        "reasons": reasons,
        "needs_confirmation": needs_confirmation,
        "detail": {"combination": source_note, "deterministic_signals": deterministic.get("signals", [])},
    }


def classify_document_confidentiality(full_text: str, document_type: Optional[str] = None,
                                       document_metadata: Optional[dict] = None,
                                       ocr_ratio: float = 0.0) -> dict:
    """Top-level entry point used by the ingestion pipeline: runs deterministic
    signal detection, the Gemini structured call, and the safe combination,
    returning what's ready to persist onto Document.confidentiality_*."""
    deterministic = detect_deterministic_signals(full_text, document_type)
    llm_result = classify_with_llm(full_text, document_metadata or {}, deterministic)
    return combine_classification(deterministic, llm_result, ocr_ratio=ocr_ratio)
