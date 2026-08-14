"""Phase 6: unit tests for the deterministic risk checks in graphrag/compliance.py,
covering section 13.4's required test list directly: unlimited liability, duplicate
clauses, unusual governing law, missing environmental/data-protection clauses
(via missing_clause detection, tested separately in test_graphrag_extraction.py's
detect_missing_clauses), plus auto-renewal and value threshold. Pure functions,
no DB/LLM needed."""

from counsel_graph.graphrag.compliance import (
    check_unusual_governing_law,
    check_value_threshold,
    detect_auto_renewal,
    detect_duplicate_clauses,
    detect_excessive_liability,
)


def test_unlimited_liability_detected():
    clauses = [{"id": "c1", "clause_type": "limitation_of_liability",
                "text": "Notwithstanding anything herein, Vendor's liability shall be unlimited liability for all claims."}]
    findings = detect_excessive_liability(clauses)
    assert len(findings) == 1
    assert findings[0]["clause_id"] == "c1"


def test_capped_liability_not_flagged_as_excessive():
    clauses = [{"id": "c1", "clause_type": "limitation_of_liability",
                "text": "Vendor's aggregate liability shall not exceed the fees paid in the prior 12 months."}]
    findings = detect_excessive_liability(clauses)
    assert findings == []


def test_non_liability_clause_not_scanned_for_liability_language():
    clauses = [{"id": "c1", "clause_type": "confidentiality",
                "text": "This mentions unlimited liability in passing but is not a liability clause."}]
    findings = detect_excessive_liability(clauses)
    assert findings == []


def test_duplicate_clauses_detected():
    clauses = [
        {"id": "c1", "clause_type": "indemnification", "text": "Indemnity clause version A."},
        {"id": "c2", "clause_type": "indemnification", "text": "Indemnity clause version B, conflicting."},
        {"id": "c3", "clause_type": "termination", "text": "Standard termination clause."},
    ]
    findings = detect_duplicate_clauses(clauses)
    assert len(findings) == 1
    assert findings[0]["clause_type"] == "indemnification"
    assert set(findings[0]["clause_ids"]) == {"c1", "c2"}


def test_exempt_clause_types_not_flagged_as_duplicate():
    """payment/service_levels/warranty legitimately repeat (e.g. milestone payments) --
    this is the 'false duplicate processing prevented' exit criterion."""
    clauses = [
        {"id": "c1", "clause_type": "payment", "text": "Milestone 1 payment terms."},
        {"id": "c2", "clause_type": "payment", "text": "Milestone 2 payment terms."},
    ]
    findings = detect_duplicate_clauses(clauses)
    assert findings == []


def test_single_occurrence_not_flagged_as_duplicate():
    clauses = [{"id": "c1", "clause_type": "termination", "text": "Standard termination clause."}]
    assert detect_duplicate_clauses(clauses) == []


def test_unusual_governing_law_detected():
    result = check_unusual_governing_law(governing_law_country="US", expected_country="IN")
    assert result is not None
    assert "US" in result["reason"]
    assert "IN" in result["reason"]


def test_matching_governing_law_not_flagged():
    assert check_unusual_governing_law(governing_law_country="IN", expected_country="IN") is None


def test_unknown_governing_law_not_flagged():
    """Never fabricate a finding from missing data."""
    assert check_unusual_governing_law(governing_law_country=None, expected_country="IN") is None
    assert check_unusual_governing_law(governing_law_country="IN", expected_country=None) is None


def test_auto_renewal_detected():
    clauses = [{"id": "c1", "clause_type": "renewal",
                "text": "This Agreement shall automatically renew for successive one-year terms unless terminated."}]
    findings = detect_auto_renewal(clauses)
    assert len(findings) == 1


def test_no_auto_renewal_language_not_flagged():
    clauses = [{"id": "c1", "clause_type": "renewal", "text": "This Agreement expires on the end date and does not renew."}]
    assert detect_auto_renewal(clauses) == []


def test_value_above_threshold_flagged():
    result = check_value_threshold(monetary_value=15_000_000, threshold=10_000_000)
    assert result is not None


def test_value_below_threshold_not_flagged():
    assert check_value_threshold(monetary_value=5_000_000, threshold=10_000_000) is None


def test_unknown_value_not_flagged():
    assert check_value_threshold(monetary_value=None) is None
