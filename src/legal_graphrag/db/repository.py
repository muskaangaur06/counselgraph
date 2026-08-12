"""Postgres-backed repository functions used by the API layer and the ingestion
pipeline: org profile config lookups (replacing the old hardcoded
_EXPECTED_CLAUSES_BY_CONTRACT_TYPE dict), document/clause/risk-flag CRUD with
content-hash dedup, review actions, and the append-only audit log.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from .dedup import clause_content_hash
from .models import (
    AuditLog,
    Clause,
    ConfidentialityOverride,
    Document,
    KnowledgeReference,
    OrgProfile,
    PlaybookEntry,
    ReviewAction,
    RiskFlag,
    SummaryVersion,
)
from .session import get_session

_DEFAULT_EXPECTED_CLAUSES = ["termination", "liability_cap", "confidentiality", "governing_law"]


def get_org_profile(profile_id: str) -> Optional[dict]:
    with get_session() as session:
        profile = session.get(OrgProfile, profile_id)
        if profile is None:
            return None
        return {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "jurisdiction_defaults": profile.jurisdiction_defaults or {},
            "required_clause_checklist": profile.required_clause_checklist or {},
            "risk_threshold_overrides": profile.risk_threshold_overrides or {},
            "confidentiality_default": profile.confidentiality_default,
        }


def list_org_profiles() -> list[dict]:
    with get_session() as session:
        profiles = session.execute(select(OrgProfile)).scalars().all()
        return [
            {
                "profile_id": p.profile_id,
                "name": p.name,
                "confidentiality_default": p.confidentiality_default,
            }
            for p in profiles
        ]


def get_expected_clauses(org_profile_id: Optional[str], contract_type: Optional[str]) -> list[str]:
    """Resolves the expected-clause checklist for a document's org profile and contract
    type. Falls back to a small generic default if the org profile has no entry for
    this contract type, or if org_profile_id/the profile itself can't be resolved."""
    key = (contract_type or "").strip().lower()
    if org_profile_id:
        profile = get_org_profile(org_profile_id)
        if profile:
            checklist = profile["required_clause_checklist"] or {}
            if key in checklist:
                return checklist[key]
            if checklist:
                # profile has a checklist but not for this contract_type: use its
                # own "default" entry if present, else fall through to the generic list
                if "default" in checklist:
                    return checklist["default"]
    return _DEFAULT_EXPECTED_CLAUSES


def get_risk_threshold_overrides(org_profile_id: Optional[str]) -> dict:
    if not org_profile_id:
        return {}
    profile = get_org_profile(org_profile_id)
    return (profile or {}).get("risk_threshold_overrides", {}) or {}


def create_document(**fields) -> str:
    with get_session() as session:
        doc = Document(**fields)
        session.add(doc)
        session.flush()
        return doc.document_id


def update_document(document_id: str, **fields) -> None:
    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return
        for k, v in fields.items():
            setattr(doc, k, v)


def get_document(document_id: str) -> Optional[dict]:
    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return None
        return {
            "document_id": doc.document_id,
            "filename": doc.filename,
            "storage_key": doc.storage_key,
            "collection_name": doc.collection_name,
            "document_type": doc.document_type,
            "business_unit": doc.business_unit,
            "counterparty": doc.counterparty,
            "geography": doc.geography,
            "confidentiality_level": doc.confidentiality_level,
            "confidentiality_confidence": doc.confidentiality_confidence,
            "confidentiality_source": doc.confidentiality_source,
            "confidentiality_reasons": doc.confidentiality_reasons,
            "confidentiality_needs_confirmation": doc.confidentiality_needs_confirmation,
            "review_priority": doc.review_priority,
            "org_profile_id": doc.org_profile_id,
            "status": doc.status,
            "job_id": doc.job_id,
            "contract_id": doc.contract_id,
        }


def upsert_clause(document_id: str, clause_type: Optional[str], extracted_text: str,
                   page_reference: Optional[str] = None, party: Optional[str] = None,
                   obligation_owner: Optional[str] = None, confidence: Optional[float] = None) -> tuple[str, bool]:
    """Upserts a clause by content_hash (normalized clause_type + party + text) scoped
    to this document_id. Returns (clause_id, created). On a re-run for the same
    document, an existing row with the same content_hash is updated (version bumped)
    instead of a fresh row being inserted, per section 2's dedup requirement."""
    content_hash = clause_content_hash(clause_type, party, extracted_text)
    with get_session() as session:
        existing = session.execute(
            select(Clause).where(Clause.document_id == document_id, Clause.content_hash == content_hash)
        ).scalar_one_or_none()

        if existing is not None:
            existing.page_reference = page_reference
            existing.obligation_owner = obligation_owner
            existing.confidence = confidence
            existing.version += 1
            session.flush()
            return existing.clause_id, False

        clause = Clause(
            document_id=document_id,
            clause_type=clause_type,
            extracted_text=extracted_text,
            page_reference=page_reference,
            party=party,
            obligation_owner=obligation_owner,
            confidence=confidence,
            content_hash=content_hash,
            version=1,
        )
        session.add(clause)
        session.flush()
        return clause.clause_id, True


def get_risk_flag(risk_flag_id: str) -> Optional[dict]:
    with get_session() as session:
        flag = session.get(RiskFlag, risk_flag_id)
        if flag is None:
            return None
        return {"risk_flag_id": flag.risk_flag_id, "clause_id": flag.clause_id,
                "assigned_role": flag.assigned_role, "reviewer_status": flag.reviewer_status}


def create_risk_flag(clause_id: str, severity: str, rationale: Optional[str] = None,
                      confidence: Optional[float] = None, recommended_action: Optional[str] = None,
                      deviation_score: Optional[float] = None, deviation_detail: Optional[dict] = None,
                      confidence_breakdown: Optional[dict] = None, assigned_role: str = "reviewer") -> str:
    with get_session() as session:
        flag = RiskFlag(
            clause_id=clause_id,
            severity=severity,
            rationale=rationale,
            confidence=confidence,
            recommended_action=recommended_action,
            deviation_score=deviation_score,
            deviation_detail=deviation_detail,
            confidence_breakdown=confidence_breakdown,
            assigned_role=assigned_role,
        )
        session.add(flag)
        session.flush()
        return flag.risk_flag_id


def create_playbook_entry(risk_flag_id: str, current_language: str, fallback_positions: list,
                           fallback_source: str, suggested_redline: Optional[str]) -> str:
    with get_session() as session:
        entry = PlaybookEntry(
            risk_flag_id=risk_flag_id,
            current_language=current_language,
            fallback_positions=fallback_positions,
            fallback_source=fallback_source,
            suggested_redline=suggested_redline,
        )
        session.add(entry)
        session.flush()
        return entry.playbook_entry_id


def record_review_action(reviewer_username: str, role: str, action: str, rationale: Optional[str] = None,
                          document_id: Optional[str] = None, clause_id: Optional[str] = None,
                          risk_flag_id: Optional[str] = None) -> str:
    with get_session() as session:
        row = ReviewAction(
            document_id=document_id, clause_id=clause_id, risk_flag_id=risk_flag_id,
            reviewer_username=reviewer_username, role=role, action=action, rationale=rationale,
        )
        session.add(row)
        session.flush()
        return row.review_action_id


def write_audit_log(stage: str, actor: str, action: str, details: Optional[dict] = None,
                     document_id: Optional[str] = None) -> str:
    with get_session() as session:
        row = AuditLog(document_id=document_id, stage=stage, actor=actor, action=action, details=details or {})
        session.add(row)
        session.flush()
        return row.audit_id


def get_audit_log(document_id: str) -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            select(AuditLog).where(AuditLog.document_id == document_id).order_by(AuditLog.timestamp.asc())
        ).scalars().all()
        return [
            {"audit_id": r.audit_id, "stage": r.stage, "actor": r.actor, "action": r.action,
             "details": r.details, "timestamp": r.timestamp.isoformat()}
            for r in rows
        ]


def add_summary_version(document_id: str, summary_text: str, edited_by: Optional[str] = None) -> dict:
    with get_session() as session:
        last = session.execute(
            select(SummaryVersion)
            .where(SummaryVersion.document_id == document_id)
            .order_by(SummaryVersion.version_number.desc())
        ).scalars().first()
        next_version = (last.version_number + 1) if last else 1
        row = SummaryVersion(
            document_id=document_id, version_number=next_version,
            summary_text=summary_text, edited_by=edited_by,
        )
        session.add(row)
        session.flush()
        return {"summary_version_id": row.summary_version_id, "version_number": row.version_number}


def get_summary_history(document_id: str) -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            select(SummaryVersion)
            .where(SummaryVersion.document_id == document_id)
            .order_by(SummaryVersion.version_number.asc())
        ).scalars().all()
        return [
            {"version_number": r.version_number, "summary_text": r.summary_text,
             "edited_by": r.edited_by, "created_at": r.created_at.isoformat()}
            for r in rows
        ]


def apply_confidentiality_classification(document_id: str, classification: dict) -> dict:
    """Persists an automatic classification result onto Document and appends a
    ConfidentialityOverride history row. Never overwrites a level a human already
    set via override -- automatic classification only applies to a document that
    doesn't have a confidentiality_source of manual_override yet."""
    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return {}
        if doc.confidentiality_source == "manual_override":
            return {"skipped": True, "reason": "document already has a manual override"}

        previous_level = doc.confidentiality_level
        doc.confidentiality_level = classification["level"]
        doc.confidentiality_confidence = classification.get("confidence")
        doc.confidentiality_source = "automatic"
        doc.confidentiality_reasons = classification.get("reasons") or []
        doc.confidentiality_needs_confirmation = bool(classification.get("needs_confirmation"))

        session.add(ConfidentialityOverride(
            document_id=document_id,
            previous_level=previous_level,
            new_level=classification["level"],
            source="automatic",
            reason=None,
            confidence=classification.get("confidence"),
            changed_by=None,
        ))
        session.flush()
        return {"document_id": document_id, "level": doc.confidentiality_level}


def override_confidentiality(document_id: str, new_level: str, reason: str, changed_by: str) -> dict:
    """Applies a human override, per section 12.3: requires new level, reason,
    user identity (timestamp is stamped automatically). Always audited via the
    ConfidentialityOverride row this writes."""
    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"No document found for document_id={document_id}")

        previous_level = doc.confidentiality_level
        doc.confidentiality_level = new_level
        doc.confidentiality_confidence = 1.0
        doc.confidentiality_source = "manual_override"
        doc.confidentiality_needs_confirmation = False

        override = ConfidentialityOverride(
            document_id=document_id,
            previous_level=previous_level,
            new_level=new_level,
            source="manual_override",
            reason=reason,
            confidence=1.0,
            changed_by=changed_by,
        )
        session.add(override)
        session.add(AuditLog(
            document_id=document_id,
            stage="confidentiality",
            actor=changed_by,
            action="manual_override",
            details={"previous_level": previous_level, "new_level": new_level, "reason": reason},
        ))
        session.flush()
        return {
            "document_id": document_id,
            "previous_level": previous_level,
            "new_level": new_level,
            "override_id": override.override_id,
        }


def get_confidentiality_history(document_id: str) -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            select(ConfidentialityOverride)
            .where(ConfidentialityOverride.document_id == document_id)
            .order_by(ConfidentialityOverride.created_at.asc())
        ).scalars().all()
        return [
            {
                "override_id": r.override_id,
                "previous_level": r.previous_level,
                "new_level": r.new_level,
                "source": r.source,
                "reason": r.reason,
                "confidence": r.confidence,
                "changed_by": r.changed_by,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


def find_knowledge_references(clause_type: Optional[str] = None, org_profile_id: Optional[str] = None,
                               business_unit: Optional[str] = None, jurisdiction: Optional[str] = None,
                               approval_status: Optional[str] = "approved") -> list[dict]:
    """Filters/ranks the clause library for RAG retrieval per guideline 5.4:
    approval_status, business_unit_scope, and jurisdiction_scope narrow the
    candidate set; exact business_unit/jurisdiction matches are ranked first."""
    with get_session() as session:
        stmt = select(KnowledgeReference)
        if clause_type:
            stmt = stmt.where(KnowledgeReference.clause_type == clause_type)
        if org_profile_id:
            stmt = stmt.where(KnowledgeReference.org_profile_id == org_profile_id)
        if approval_status:
            stmt = stmt.where(KnowledgeReference.approval_status == approval_status)
        rows = session.execute(stmt).scalars().all()

        def _rank(r: KnowledgeReference) -> tuple:
            bu_match = 1 if (business_unit and r.business_unit_scope == business_unit) else 0
            j_match = 1 if (jurisdiction and r.jurisdiction_scope == jurisdiction) else 0
            return (-bu_match, -j_match, -r.version)

        ranked = sorted(rows, key=_rank)
        return [
            {
                "knowledge_reference_id": r.knowledge_reference_id,
                "clause_type": r.clause_type,
                "title": r.title,
                "reference_text": r.reference_text,
                "source_kind": r.source_kind,
                "approval_status": r.approval_status,
                "version": r.version,
                "business_unit_scope": r.business_unit_scope,
                "jurisdiction_scope": r.jurisdiction_scope,
                "extra": r.extra,
            }
            for r in ranked
        ]


def get_approved_clause_language(clause_type: str, org_profile_id: Optional[str] = None) -> Optional[str]:
    """Returns the best-matching approved clause text for a clause_type, used by
    section 4's deviation scoring and section 5's playbook fallback positions."""
    refs = find_knowledge_references(clause_type=clause_type, org_profile_id=org_profile_id, approval_status="approved")
    refs = [r for r in refs if r["source_kind"] == "approved_clause"]
    return refs[0]["reference_text"] if refs else None
