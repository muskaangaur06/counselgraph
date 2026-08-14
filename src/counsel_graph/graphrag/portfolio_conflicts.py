"""Section 6: Cross-Portfolio Conflict Detection.

Finds clauses of the same clause_type across different documents under the
same org profile or counterparty with materially different terms (e.g.
termination notice period differing by more than a threshold). Numeric terms
(day/month/year counts) are extracted with a regex heuristic first since that
covers the common case (notice periods, cure periods, liability caps in
months of fees) without another LLM call; the caller decides the threshold.
"""

from __future__ import annotations

import re
from itertools import combinations
from typing import Optional

_UNIT_TO_DAYS = {"day": 1, "days": 1, "month": 30, "months": 30, "year": 365, "years": 365}

# contract drafting conventions spell numbers out in words with the digit form
# parenthesized afterward ("thirty (30) days"), so the digits are captured from
# inside the parens; a plain "30 days" (no spelled-out word) is matched too.
_NUMBER_UNIT_RE = re.compile(
    r"\b\(?(\d+)\)?\s*(day|days|month|months|year|years)\b", re.IGNORECASE
)


def extract_numeric_terms_in_days(text: str) -> list[int]:
    """Extracts every "N day(s)/month(s)/year(s)" mention, normalized to days,
    e.g. "thirty (30) days" -> 30, "90 days" -> 90, "1 year" -> 365."""
    values = []
    for match in _NUMBER_UNIT_RE.finditer(text):
        n, unit = match.groups()
        values.append(int(n) * _UNIT_TO_DAYS[unit.lower()])
    return values


def _materially_different(text_a: str, text_b: str, threshold_days: int) -> Optional[dict]:
    terms_a = extract_numeric_terms_in_days(text_a)
    terms_b = extract_numeric_terms_in_days(text_b)
    if not terms_a or not terms_b:
        return None
    # compare the largest extracted term from each side (the notice/cure period
    # is usually the standout number in a termination/liability clause)
    value_a, value_b = max(terms_a), max(terms_b)
    if abs(value_a - value_b) < threshold_days:
        return None
    return {"value_a_days": value_a, "value_b_days": value_b, "delta_days": abs(value_a - value_b)}


def find_conflicting_clause_pairs(clause_rows: list[dict], threshold_days: int = 15) -> list[dict]:
    """clause_rows: [{"contract_id","contract_name","clause_id","clause_type","text"}, ...]
    already scoped by the caller to one org profile or counterparty. Groups by
    clause_type, compares every cross-document pair within a type, and returns
    pairs whose extracted numeric terms differ by more than threshold_days."""
    by_type: dict[str, list[dict]] = {}
    for row in clause_rows:
        by_type.setdefault(row["clause_type"], []).append(row)

    conflicts = []
    for clause_type, rows in by_type.items():
        for a, b in combinations(rows, 2):
            if a["contract_id"] == b["contract_id"]:
                continue  # only cross-document comparisons matter here
            diff = _materially_different(a["text"], b["text"], threshold_days)
            if diff is None:
                continue
            conflicts.append({
                "clause_type": clause_type,
                "document_a": {"contract_id": a["contract_id"], "contract_name": a["contract_name"],
                                "clause_id": a["clause_id"], "text": a["text"]},
                "document_b": {"contract_id": b["contract_id"], "contract_name": b["contract_name"],
                                "clause_id": b["clause_id"], "text": b["text"]},
                **diff,
            })
    return conflicts
