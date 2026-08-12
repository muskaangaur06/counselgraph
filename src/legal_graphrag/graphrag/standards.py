"""Deterministic standards-resolution service (blueprint section 11). Resolves
the applicable approved-clause/policy standard for a document's context by
walking the 8-level precedence hierarchy from most specific to most general,
and applies section 11.2's conflict/override rules. No LLM call: this is a
pure lookup/ranking function over KnowledgeReference rows already scoped by
find_standards_candidates()'s tenant-safe SQL filter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Ordered most specific -> most general, matching section 11.1 exactly. Each
# entry is (scope_level_name, requires) where requires lists which resolved
# context keys must be present (non-None) for this level to even be tried.
_HIERARCHY = [
    ("business_unit_jurisdiction_document_type", ("business_unit_id", "jurisdiction_id", "document_type")),
    ("business_unit_document_type", ("business_unit_id", "document_type")),
    ("organization_jurisdiction_document_type", ("org_profile_id", "jurisdiction_id", "document_type")),
    ("organization_document_type", ("org_profile_id", "document_type")),
    ("customer_jurisdiction_document_type", ("customer_id", "jurisdiction_id", "document_type")),
    ("customer_document_type", ("customer_id", "document_type")),
    ("jurisdiction_only", ("jurisdiction_id",)),
    ("customer_group_fallback", ()),
]


def _parse_iso(value: str) -> datetime:
    """SQLite (unlike Postgres) doesn't preserve tzinfo through DateTime(timezone=True),
    so a round-tripped isoformat string here may be naive -- treat naive as UTC,
    matching how _now()/resolve_standards' as_of default are always UTC-aware."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_expired(row: dict, as_of: datetime) -> bool:
    if not row.get("expiry_date"):
        return False
    return _parse_iso(row["expiry_date"]) < as_of


def _is_not_yet_effective(row: dict, as_of: datetime) -> bool:
    if not row.get("effective_date"):
        return False
    return _parse_iso(row["effective_date"]) > as_of


# business_unit_id and jurisdiction_id are each strictly more specific than
# org_profile_id/customer_id (a business unit belongs to one org; having
# org_profile_id set alongside it is normal, not "extra" scoping). Only these
# two fields can make a row "too specific" for a given level -- org_profile_id
# and customer_id are ancestry, not additional narrowing, so they're never
# checked for over-specificity.
_SPECIFICITY_FIELDS = ("business_unit_id", "jurisdiction_id")


def _matches_level(row: dict, level_requires: tuple, context: dict) -> bool:
    """A row matches a hierarchy level if every field that level cares about is
    set on both the row and the context AND they're equal, and the row has no
    MORE specific scoping (business_unit_id/jurisdiction_id) set than what this
    level checks -- otherwise a row scoped to a business_unit would incorrectly
    match at the org-only level too, even though it belongs at the more specific
    business_unit level.

    document_type compatibility is checked unconditionally, independent of which
    level is being tried: a row scoped to "supply" must never match a "service"
    document context, even at a level that doesn't itself require document_type
    (e.g. jurisdiction_only, customer_group_fallback). A row with no
    document_type set is a wildcard -- applies to every document type."""
    if row.get("document_type") is not None and row.get("document_type") != context.get("document_type"):
        return False
    for field in level_requires:
        if field == "document_type":
            continue  # already checked above, unconditionally
        if row.get(field) != context.get(field):
            return False
    for field in _SPECIFICITY_FIELDS:
        if field not in level_requires and row.get(field) is not None:
            return False
    return True


def resolve_standards(clause_type: Optional[str], document_type: Optional[str],
                       org_profile_id: Optional[str], business_unit_id: Optional[str],
                       jurisdiction_id: Optional[str], customer_id: Optional[str],
                       as_of: Optional[datetime] = None) -> dict:
    """Walks the 8-level hierarchy and returns the first level with at least one
    matching, active, approved standard. Returns:
      {selected: [...], fallback: [...], scope_level, source, version,
       approval_status, reason, conflicts: [...]}
    selected/fallback/conflicts entries are the KnowledgeReference dicts from
    find_standards_candidates(). Returns scope_level=None, selected=[] if
    nothing at all applies (never fabricates a standard)."""
    as_of = as_of or datetime.now(timezone.utc)
    context = {
        "org_profile_id": org_profile_id, "business_unit_id": business_unit_id,
        "jurisdiction_id": jurisdiction_id, "document_type": document_type, "customer_id": customer_id,
    }

    from ..db.repository import find_standards_candidates
    candidates = find_standards_candidates(
        clause_type=clause_type, document_type=document_type, org_profile_id=org_profile_id,
        business_unit_id=business_unit_id, jurisdiction_id=jurisdiction_id, customer_id=customer_id,
        include_unapproved=True,  # excluded below by _is_active_and_approved, but visible for the conflict log
    )

    def _is_active_and_approved(row: dict) -> bool:
        if row["approval_status"] != "approved":
            return False
        if _is_expired(row, as_of) or _is_not_yet_effective(row, as_of):
            return False
        return True

    excluded_inactive = [r for r in candidates if not _is_active_and_approved(r)]

    for scope_level, level_requires in _HIERARCHY:
        if any(context.get(f) is None for f in level_requires if f != "document_type"):
            continue  # this level needs a non-document_type context field we don't have -- skip, don't fabricate a match

        matched = [r for r in candidates if _matches_level(r, level_requires, context) and _is_active_and_approved(r)]
        if not matched:
            continue

        # section 11.2: specific active rule overrides general active rule for the
        # SAME rule key (here, rule key = clause_type + source_kind); within this
        # single level, multiple non-conflicting rows can co-exist (e.g. one
        # approved_clause + one risk_taxonomy entry), but two DIFFERENT
        # reference_text values for the same clause_type at the same level is a
        # real conflict, not something to silently merge.
        by_key: dict[tuple, list[dict]] = {}
        for r in matched:
            by_key.setdefault((r["clause_type"], r["source_kind"]), []).append(r)

        selected = []
        conflicts = []
        for key, rows in by_key.items():
            distinct_texts = {r["reference_text"] for r in rows}
            if len(distinct_texts) > 1:
                # highest version wins deterministically; the rest are logged as
                # conflicts for the reviewer, never silently combined
                rows_sorted = sorted(rows, key=lambda r: r["version"], reverse=True)
                selected.append(rows_sorted[0])
                conflicts.extend(rows_sorted[1:])
            else:
                selected.append(rows[0])

        return {
            "selected": selected,
            "fallback": [r for r in candidates if r not in matched and _is_active_and_approved(r)],
            "scope_level": scope_level,
            "source": selected[0]["knowledge_reference_id"] if len(selected) == 1 else [s["knowledge_reference_id"] for s in selected],
            "version": selected[0]["version"] if len(selected) == 1 else None,
            "approval_status": "approved",
            "reason": f"Resolved at scope level '{scope_level}' -- the most specific level with an active, approved standard for this context.",
            "conflicts": conflicts,
            "excluded_inactive": excluded_inactive,
        }

    return {
        "selected": [], "fallback": [], "scope_level": None, "source": None, "version": None,
        "approval_status": None, "reason": "No standard found at any hierarchy level for this context.",
        "conflicts": [], "excluded_inactive": excluded_inactive,
    }


def check_mandatory_and_prohibited(clause_type: Optional[str], document_type: Optional[str],
                                    org_profile_id: Optional[str], business_unit_id: Optional[str],
                                    jurisdiction_id: Optional[str], customer_id: Optional[str],
                                    as_of: Optional[datetime] = None) -> dict:
    """Section 11.2: jurisdiction-mandatory requirements can't be removed by a
    less-specific business preference, and prohibited clauses stay prohibited
    unless an authorized explicit exception exists. Returns
    {mandatory: [...], prohibited: [...]} -- both lists of active, approved
    KnowledgeReference rows regardless of which hierarchy level would normally
    win, since a mandatory/prohibited flag is absolute, not rank-dependent."""
    as_of = as_of or datetime.now(timezone.utc)
    from ..db.repository import find_standards_candidates
    candidates = find_standards_candidates(
        clause_type=clause_type, document_type=document_type, org_profile_id=org_profile_id,
        business_unit_id=business_unit_id, jurisdiction_id=jurisdiction_id, customer_id=customer_id,
    )

    def _active(row: dict) -> bool:
        if _is_expired(row, as_of) or _is_not_yet_effective(row, as_of):
            return False
        return True

    mandatory = [r for r in candidates if r["is_mandatory"] and _active(r)]
    prohibited = [r for r in candidates if r["is_prohibited"] and _active(r)]
    return {"mandatory": mandatory, "prohibited": prohibited}


def find_all_mandatory_and_prohibited(document_type: Optional[str], org_profile_id: Optional[str],
                                       business_unit_id: Optional[str], jurisdiction_id: Optional[str],
                                       customer_id: Optional[str], as_of: Optional[datetime] = None) -> dict:
    """Like check_mandatory_and_prohibited, but scans every clause_type at once
    instead of checking one at a time -- needed to discover a mandatory
    requirement whose clause_type is ABSENT from the document (there is no
    known clause_type to ask about in that case, since nothing was extracted
    for it). Returns {mandatory: [...], prohibited: [...]}, each row still
    tagged with its own clause_type so the caller can group by it."""
    as_of = as_of or datetime.now(timezone.utc)
    from ..db.repository import find_standards_candidates
    candidates = find_standards_candidates(
        clause_type=None, document_type=document_type, org_profile_id=org_profile_id,
        business_unit_id=business_unit_id, jurisdiction_id=jurisdiction_id, customer_id=customer_id,
    )

    def _active(row: dict) -> bool:
        if _is_expired(row, as_of) or _is_not_yet_effective(row, as_of):
            return False
        return True

    mandatory = [r for r in candidates if r["is_mandatory"] and _active(r)]
    prohibited = [r for r in candidates if r["is_prohibited"] and _active(r)]
    return {"mandatory": mandatory, "prohibited": prohibited}
