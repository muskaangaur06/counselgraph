"""Phase 4: unit tests for the deterministic signal detector and the safe
combination logic in graphrag/confidentiality.py. No Gemini/DB needed -- these
exercise pure functions against section 12.5's minimum test list."""

from legal_graphrag.graphrag.confidentiality import (
    combine_classification,
    detect_deterministic_signals,
)


def test_ma_document_detects_highly_confidential_signal():
    text = "This memorandum discusses the proposed merger and acquisition, including valuation details."
    signals = detect_deterministic_signals(text)
    assert signals["suggested_level"] == "highly_confidential"


def test_nda_detects_confidential_signal():
    text = "This Non-Disclosure Agreement governs the exchange of confidential information."
    signals = detect_deterministic_signals(text)
    assert "nda" in signals["signals"]


def test_explicit_strictly_confidential_label_detected():
    text = "STRICTLY CONFIDENTIAL - Board Materials Only"
    signals = detect_deterministic_signals(text)
    assert signals["explicit_level"] == "highly_confidential"


def test_internal_use_only_label_detected():
    text = "INTERNAL USE ONLY. Standard operating procedure for vendor onboarding."
    signals = detect_deterministic_signals(text)
    assert signals["explicit_level"] == "internal"


def test_no_signals_found_suggests_no_level():
    text = "This is a plain document with no special keywords in it at all."
    signals = detect_deterministic_signals(text)
    assert signals["explicit_level"] is None
    assert signals["suggested_level"] is None


def test_explicit_label_cannot_be_downgraded_by_llm():
    deterministic = {"explicit_level": "highly_confidential", "explicit_evidence": "strictly confidential", "signals": []}
    llm_result = {"level": "internal", "confidence": 0.9, "reasons": []}
    combined = combine_classification(deterministic, llm_result)
    assert combined["level"] == "highly_confidential"


def test_disagreement_by_more_than_one_level_flags_confirmation():
    deterministic = {"explicit_level": None, "suggested_level": "public", "signals": []}
    llm_result = {"level": "highly_confidential", "confidence": 0.8, "reasons": []}
    combined = combine_classification(deterministic, llm_result)
    assert combined["needs_confirmation"] is True
    assert combined["level"] == "highly_confidential"  # safer level wins


def test_low_ocr_confidence_reduces_final_confidence():
    deterministic = {"explicit_level": None, "suggested_level": "internal", "signals": []}
    llm_result = {"level": "internal", "confidence": 0.9, "reasons": []}
    combined = combine_classification(deterministic, llm_result, ocr_ratio=0.9)
    assert combined["confidence"] <= 0.4
    assert combined["needs_confirmation"] is True


def test_low_ocr_document_does_not_become_public():
    deterministic = {"explicit_level": None, "suggested_level": None, "signals": []}
    llm_result = {"level": "public", "confidence": 0.95, "reasons": []}
    combined = combine_classification(deterministic, llm_result, ocr_ratio=0.9)
    assert combined["level"] != "public"


def test_low_confidence_never_silently_public():
    deterministic = {"explicit_level": None, "suggested_level": None, "signals": []}
    llm_result = {"level": "public", "confidence": 0.3, "reasons": []}
    combined = combine_classification(deterministic, llm_result)
    assert combined["level"] == "internal"
    assert combined["needs_confirmation"] is True


def test_annual_report_with_high_confidence_can_stay_public():
    deterministic = {"explicit_level": None, "suggested_level": "public", "signals": []}
    llm_result = {"level": "public", "confidence": 0.95, "reasons": []}
    combined = combine_classification(deterministic, llm_result)
    assert combined["level"] == "public"
    assert combined["needs_confirmation"] is False
