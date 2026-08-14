#!/usr/bin/env python
"""
Creates Postgres tables (if missing) and seeds:
- 2 org profiles ("Vendor Procurement Unit", "Cross-Border Compliance Unit") with
  different required_clause_checklists and risk_threshold_overrides
- knowledge_reference rows migrated from data/clause_library/approved_clauses.json
  and data/risk_taxonomy.csv, tagged to those profiles

Safe to run multiple times: existing profiles/rows with the same name/text are
left as-is rather than duplicated.

Usage:
    python scripts/seed_db.py
"""

from __future__ import annotations

import csv
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from sqlalchemy import select  # noqa: E402

from counsel_graph.db.session import get_session, init_db  # noqa: E402
from counsel_graph.db.models import KnowledgeReference, OrgProfile  # noqa: E402

CLAUSE_LIBRARY_PATH = os.path.join(_REPO_ROOT, "data", "clause_library", "approved_clauses.json")
RISK_TAXONOMY_PATH = os.path.join(_REPO_ROOT, "data", "risk_taxonomy.csv")

VENDOR_PROCUREMENT_CHECKLIST = {
    "outsourcing": ["termination", "liability_cap", "confidentiality", "indemnification", "governing_law"],
    "service": ["termination", "liability_cap", "confidentiality", "governing_law"],
    "supply": ["termination", "liability_cap", "confidentiality", "governing_law", "audit_rights"],
    "maintenance": ["termination", "liability_cap", "confidentiality", "governing_law"],
    "default": ["termination", "liability_cap", "confidentiality", "governing_law"],
}
VENDOR_PROCUREMENT_RISK_OVERRIDES = {
    "senior_counsel_severity": "high",
    "senior_counsel_confidence_floor": 0.6,
}

CROSS_BORDER_CHECKLIST = {
    "outsourcing": ["termination", "liability_cap", "confidentiality", "indemnification", "governing_law", "data_protection"],
    "service": ["termination", "liability_cap", "confidentiality", "governing_law", "data_protection"],
    "license agreement": ["termination", "confidentiality", "governing_law", "data_protection"],
    "default": ["termination", "liability_cap", "confidentiality", "governing_law", "data_protection"],
}
CROSS_BORDER_RISK_OVERRIDES = {
    # cross-border compliance is stricter: escalate to senior_counsel more readily
    "senior_counsel_severity": "medium",
    "senior_counsel_confidence_floor": 0.75,
}

GENERAL_COUNSEL_CHECKLIST = {
    "default": ["termination", "liability_cap", "confidentiality", "governing_law"],
}
GENERAL_COUNSEL_RISK_OVERRIDES = {
    "senior_counsel_severity": "high",
    "senior_counsel_confidence_floor": 0.5,
}

PROFILE_SEEDS = [
    {
        "name": "Vendor Procurement Unit",
        "jurisdiction_defaults": {"governing_law_country": "IN"},
        "required_clause_checklist": VENDOR_PROCUREMENT_CHECKLIST,
        "risk_threshold_overrides": VENDOR_PROCUREMENT_RISK_OVERRIDES,
        "confidentiality_default": "internal",
    },
    {
        "name": "Cross-Border Compliance Unit",
        "jurisdiction_defaults": {"governing_law_country": "IN"},
        "required_clause_checklist": CROSS_BORDER_CHECKLIST,
        "risk_threshold_overrides": CROSS_BORDER_RISK_OVERRIDES,
        "confidentiality_default": "restricted",
    },
    {
        "name": "General Counsel Office",
        "jurisdiction_defaults": {"governing_law_country": "IN"},
        "required_clause_checklist": GENERAL_COUNSEL_CHECKLIST,
        "risk_threshold_overrides": GENERAL_COUNSEL_RISK_OVERRIDES,
        "confidentiality_default": "internal",
    },
]


def seed_org_profiles() -> dict[str, str]:
    """Returns {profile_name: profile_id}."""
    name_to_id: dict[str, str] = {}
    with get_session() as session:
        for seed in PROFILE_SEEDS:
            existing = session.execute(
                select(OrgProfile).where(OrgProfile.name == seed["name"])
            ).scalar_one_or_none()
            if existing is not None:
                name_to_id[seed["name"]] = existing.profile_id
                continue
            profile = OrgProfile(**seed)
            session.add(profile)
            session.flush()
            name_to_id[seed["name"]] = profile.profile_id
            print(f"  created org_profile: {seed['name']} ({profile.profile_id})")
    return name_to_id


def seed_knowledge_references(profile_ids: dict[str, str]) -> None:
    if not os.path.exists(CLAUSE_LIBRARY_PATH):
        print(f"  skipped: {CLAUSE_LIBRARY_PATH} not found")
    else:
        with open(CLAUSE_LIBRARY_PATH, encoding="utf-8") as f:
            approved_clauses = json.load(f)

        with get_session() as session:
            for profile_name, profile_id in profile_ids.items():
                for entry in approved_clauses:
                    existing = session.execute(
                        select(KnowledgeReference).where(
                            KnowledgeReference.org_profile_id == profile_id,
                            KnowledgeReference.clause_type == entry["clause_type"],
                            KnowledgeReference.source_kind == "approved_clause",
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        continue
                    session.add(KnowledgeReference(
                        org_profile_id=profile_id,
                        clause_type=entry["clause_type"],
                        title=entry.get("title"),
                        reference_text=entry["approved_text"],
                        source_kind="approved_clause",
                        approval_status="approved",
                        version=1,
                        business_unit_scope=entry.get("business_unit"),
                        jurisdiction_scope=entry.get("jurisdiction"),
                        extra={"owner": entry.get("owner"), "notes": entry.get("notes"),
                               "approval_date": entry.get("approval_date")},
                    ))
        print(f"  migrated {len(approved_clauses)} approved_clauses entries per org profile")

    if not os.path.exists(RISK_TAXONOMY_PATH):
        print(f"  skipped: {RISK_TAXONOMY_PATH} not found")
        return

    with open(RISK_TAXONOMY_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with get_session() as session:
        for profile_name, profile_id in profile_ids.items():
            for row in rows:
                existing = session.execute(
                    select(KnowledgeReference).where(
                        KnowledgeReference.org_profile_id == profile_id,
                        KnowledgeReference.title == row["risk_category"],
                        KnowledgeReference.source_kind == "risk_taxonomy",
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue
                session.add(KnowledgeReference(
                    org_profile_id=profile_id,
                    clause_type=row.get("typical_clause_types"),
                    title=row["risk_category"],
                    reference_text=row["description"],
                    source_kind="risk_taxonomy",
                    approval_status="approved",
                    version=1,
                    extra={"severity": row["severity"], "recommended_reviewer_action": row["recommended_reviewer_action"]},
                ))
    print(f"  migrated {len(rows)} risk_taxonomy rows per org profile")


def main() -> None:
    print("Creating tables (if missing)...")
    init_db()

    print("Seeding org profiles...")
    profile_ids = seed_org_profiles()

    print("Seeding knowledge_reference rows from clause_library/risk_taxonomy...")
    seed_knowledge_references(profile_ids)

    print("\nDone. Org profiles:")
    for name, pid in profile_ids.items():
        print(f"  {name}: {pid}")


if __name__ == "__main__":
    main()
