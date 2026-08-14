"""Section 13.4: deterministic risk checks that don't need an LLM call -- run
alongside flag_risks()'s LLM-based review, not instead of it. Each check
returns risk-flag-shaped dicts tagged with a category, so risk_flag_node can
persist them the same way as LLM findings, just with applicable_rule_source
set to "deterministic:<check_name>" instead of "llm_risk_review".
"""

from __future__ import annotations

import re
from typing import Optional

# clause_type values that legitimately, expectedly repeat in one contract
# (e.g. multiple "payment" clauses for different milestones) -- duplicate
# detection only flags types where a second occurrence is itself the anomaly
_DUPLICATE_EXEMPT_CLAUSE_TYPES = {"payment", "service_levels", "warranty"}

_AUTO_RENEWAL_PATTERN = re.compile(
    r"\b(automatically renew|auto-renew|automatic renewal|shall renew unless|"
    r"evergreen)\b", re.IGNORECASE,
)
_UNLIMITED_LIABILITY_PATTERN = re.compile(
    r"\b(unlimited liability|no limit(?:ation)? (?:on|of) liability|"
    r"liability shall not be limited|uncapped liability)\b", re.IGNORECASE,
)


def detect_duplicate_clauses(clauses: list[dict]) -> list[dict]:
    """Flags clause_types that appear more than once in the SAME document, for
    types where repetition is itself the anomaly (not payment schedules etc).
    Returns [{"clause_ids": [...], "clause_type": str, "reason": str}, ...]."""
    by_type: dict[str, list[dict]] = {}
    for c in clauses:
        ctype = (c.get("clause_type") or "").strip().lower()
        if not ctype or ctype in _DUPLICATE_EXEMPT_CLAUSE_TYPES:
            continue
        by_type.setdefault(ctype, []).append(c)

    findings = []
    for ctype, group in by_type.items():
        if len(group) > 1:
            findings.append({
                "clause_ids": [c["id"] for c in group],
                "clause_type": ctype,
                "reason": f"{len(group)} separate '{ctype}' clauses found in this document -- "
                          f"likely a drafting error (conflicting or redundant versions) rather than intentional.",
            })
    return findings


def detect_auto_renewal(clauses: list[dict]) -> list[dict]:
    """Flags clauses whose text contains an automatic-renewal / evergreen pattern.
    Returns [{"clause_id", "clause_type", "evidence"}, ...]."""
    findings = []
    for c in clauses:
        text = c.get("text", "")
        match = _AUTO_RENEWAL_PATTERN.search(text)
        if match:
            findings.append({"clause_id": c["id"], "clause_type": c.get("clause_type"), "evidence": match.group(0)})
    return findings


def detect_excessive_liability(clauses: list[dict]) -> list[dict]:
    """Deterministic backstop for unmistakably unlimited/uncapped liability
    language, independent of the LLM risk review (which may miss it or phrase
    the finding differently) -- a specific, high-confidence keyword match.
    Returns [{"clause_id", "clause_type", "evidence"}, ...]."""
    findings = []
    for c in clauses:
        if (c.get("clause_type") or "").strip().lower() not in ("limitation_of_liability", "liability_cap", "liability"):
            continue
        text = c.get("text", "")
        match = _UNLIMITED_LIABILITY_PATTERN.search(text)
        if match:
            findings.append({"clause_id": c["id"], "clause_type": c.get("clause_type"), "evidence": match.group(0)})
    return findings


def check_value_threshold(monetary_value: Optional[float], threshold: float = 10_000_000) -> Optional[dict]:
    """Section 13.4's "value threshold" category: contracts above a configured
    monetary value warrant a flag regardless of clause-level content, since
    high-value deals typically need extra scrutiny/approval. threshold is in
    the contract's stated currency's numeric units (no FX conversion here --
    same limitation as contract_metadata.py's monetary_value extraction)."""
    if monetary_value is None or monetary_value < threshold:
        return None
    return {
        "monetary_value": monetary_value, "threshold": threshold,
        "reason": f"Contract value ({monetary_value:,.0f}) exceeds the {threshold:,.0f} review threshold.",
    }


def check_unusual_governing_law(governing_law_country: Optional[str], expected_country: Optional[str]) -> Optional[dict]:
    """Section 13.4's "unusual governing law" category: the document's governing
    law doesn't match the org profile's expected jurisdiction. Returns None if
    either side is unknown (never fabricate a finding from missing data) or if
    they match."""
    if not governing_law_country or not expected_country:
        return None
    if governing_law_country.strip().upper() == expected_country.strip().upper():
        return None
    return {
        "governing_law_country": governing_law_country, "expected_country": expected_country,
        "reason": f"Governing law is {governing_law_country}, but this organization's expected "
                  f"jurisdiction is {expected_country}.",
    }
