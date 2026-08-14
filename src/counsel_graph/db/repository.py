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
    ApprovalStep,
    AuditLog,
    ChatMessage,
    Clause,
    ConfidentialityOverride,
    DecisionBrief,
    Document,
    EvaluationCaseResult,
    EvaluationRun,
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
            "customer_id": profile.customer_id,
            "name": profile.name,
            "industry_sector": profile.industry_sector,
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
            "parties": doc.parties,
            "subject_matter": doc.subject_matter,
            "effective_date": doc.effective_date,
            "end_date": doc.end_date,
            "monetary_value": doc.monetary_value,
            "governing_law_country": doc.governing_law_country,
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
        return {"risk_flag_id": flag.risk_flag_id, "clause_id": flag.clause_id, "document_id": flag.document_id,
                "category": flag.category, "assigned_role": flag.assigned_role, "reviewer_status": flag.reviewer_status}


def create_risk_flag(clause_id: Optional[str], severity: str, rationale: Optional[str] = None,
                      confidence: Optional[float] = None, recommended_action: Optional[str] = None,
                      deviation_score: Optional[float] = None, deviation_detail: Optional[dict] = None,
                      confidence_breakdown: Optional[dict] = None, assigned_role: str = "reviewer",
                      document_id: Optional[str] = None, category: Optional[str] = None,
                      standards_evidence: Optional[dict] = None, applicable_rule_source: Optional[str] = None) -> str:
    """clause_id is optional: document-level categories (missing_clause, compliance_gap,
    unusual_governing_law) have no single clause to attach to and set document_id instead."""
    with get_session() as session:
        flag = RiskFlag(
            clause_id=clause_id,
            document_id=document_id,
            category=category,
            severity=severity,
            rationale=rationale,
            confidence=confidence,
            recommended_action=recommended_action,
            deviation_score=deviation_score,
            deviation_detail=deviation_detail,
            confidence_breakdown=confidence_breakdown,
            standards_evidence=standards_evidence,
            applicable_rule_source=applicable_rule_source,
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


def get_playbook_entries_for_document(document_id: str) -> list[dict]:
    """One joined query for every playbook entry belonging to a document, covering
    both clause-level risk flags (via Clause.document_id) and document-level risk
    flags (via RiskFlag.document_id directly) -- the same union
    api/main.py's get_document_detail already does across two separate queries,
    but here as a single join instead of the N+1 per-flag lookup _flag_payload
    does today."""
    with get_session() as session:
        clause_level = session.execute(
            select(PlaybookEntry, RiskFlag, Clause.clause_type)
            .join(RiskFlag, PlaybookEntry.risk_flag_id == RiskFlag.risk_flag_id)
            .join(Clause, RiskFlag.clause_id == Clause.clause_id)
            .where(Clause.document_id == document_id)
        ).all()
        document_level = session.execute(
            select(PlaybookEntry, RiskFlag, RiskFlag.category)
            .join(RiskFlag, PlaybookEntry.risk_flag_id == RiskFlag.risk_flag_id)
            .where(RiskFlag.document_id == document_id, RiskFlag.clause_id.is_(None))
        ).all()

        entries = []
        for playbook, flag, clause_type in list(clause_level) + list(document_level):
            entries.append({
                "risk_flag_id": flag.risk_flag_id,
                "clause_type": clause_type,
                "category": flag.category,
                "severity": flag.severity,
                "confidence": flag.confidence,
                "rationale": flag.rationale,
                "assigned_role": flag.assigned_role,
                "current_language": playbook.current_language,
                "fallback_positions": playbook.fallback_positions,
                "fallback_source": playbook.fallback_source,
                "suggested_redline": playbook.suggested_redline,
            })
        return entries


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


def _summary_version_dict(r: "SummaryVersion") -> dict:
    return {
        "summary_version_id": r.summary_version_id, "version_number": r.version_number,
        "summary_text": r.summary_text, "edited_by": r.edited_by, "is_ai_generated": r.is_ai_generated,
        "approval_status": r.approval_status, "approved_by": r.approved_by,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "restored_from_version": r.restored_from_version, "created_at": r.created_at.isoformat(),
    }


def add_summary_version(document_id: str, summary_text: str, edited_by: Optional[str] = None,
                         is_ai_generated: Optional[bool] = None, restored_from_version: Optional[int] = None) -> dict:
    """Always creates a new row (section 14.1: never overwrite history, approved
    or not -- there is no update path for an existing SummaryVersion at all).
    is_ai_generated defaults to True only when edited_by is None (the initial
    ingestion-time generation); an explicit human edit or restore always sets
    is_ai_generated=False unless the caller says otherwise, since a human took
    an editorial action even if the resulting text happens to be AI-authored."""
    if is_ai_generated is None:
        is_ai_generated = edited_by is None
    with get_session() as session:
        last = session.execute(
            select(SummaryVersion)
            .where(SummaryVersion.document_id == document_id)
            .order_by(SummaryVersion.version_number.desc())
        ).scalars().first()
        next_version = (last.version_number + 1) if last else 1
        row = SummaryVersion(
            document_id=document_id, version_number=next_version,
            summary_text=summary_text, edited_by=edited_by, is_ai_generated=is_ai_generated,
            restored_from_version=restored_from_version,
        )
        session.add(row)
        session.flush()
        return _summary_version_dict(row)


def get_summary_history(document_id: str) -> list[dict]:
    """Ascending by version_number (oldest first) -- callers that want the
    LATEST version should read the last element, not the first. See
    get_latest_summary_version() for that case."""
    with get_session() as session:
        rows = session.execute(
            select(SummaryVersion)
            .where(SummaryVersion.document_id == document_id)
            .order_by(SummaryVersion.version_number.asc())
        ).scalars().all()
        return [_summary_version_dict(r) for r in rows]


def get_latest_summary_version(document_id: str) -> Optional[dict]:
    with get_session() as session:
        row = session.execute(
            select(SummaryVersion)
            .where(SummaryVersion.document_id == document_id)
            .order_by(SummaryVersion.version_number.desc())
        ).scalars().first()
        return _summary_version_dict(row) if row else None


def approve_summary_version(document_id: str, version_number: int, approved_by: str) -> dict:
    """Marks a specific version approved -- does not touch any other version's row
    (multiple versions can each independently be approved/unapproved over time;
    this only ever sets fields on the row being approved, never creates or
    deletes rows, consistent with the append-only convention)."""
    from datetime import datetime, timezone
    with get_session() as session:
        row = session.execute(
            select(SummaryVersion).where(
                SummaryVersion.document_id == document_id, SummaryVersion.version_number == version_number,
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"No summary version {version_number} found for document_id={document_id}")
        row.approval_status = "approved"
        row.approved_by = approved_by
        row.approved_at = datetime.now(timezone.utc)
        session.flush()
        return _summary_version_dict(row)


def restore_summary_version(document_id: str, version_number: int, restored_by: str) -> dict:
    """Restoring a previous version creates a NEW version with that version's
    text (never rewinds/mutates the version being restored, or deletes the
    versions created after it) -- so the full history, including the fact that
    a restore happened and from which version, remains visible."""
    with get_session() as session:
        source = session.execute(
            select(SummaryVersion).where(
                SummaryVersion.document_id == document_id, SummaryVersion.version_number == version_number,
            )
        ).scalar_one_or_none()
        if source is None:
            raise ValueError(f"No summary version {version_number} found for document_id={document_id}")
        source_text = source.summary_text
    return add_summary_version(document_id, source_text, edited_by=restored_by,
                                is_ai_generated=False, restored_from_version=version_number)


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


def get_approved_clause_language(clause_type: str, org_profile_id: Optional[str] = None,
                                  standards_context: Optional[dict] = None) -> Optional[str]:
    """Returns the best-matching approved clause text for a clause_type, used by
    section 4's deviation scoring and section 5's playbook fallback positions.
    When standards_context (business_unit_id/jurisdiction_id/document_type/
    customer_id) is given, resolves through the Phase 5 8-level hierarchy
    (graphrag/standards.py) instead of the simple org_profile_id-only lookup --
    same clause_type, same org_profile_id, but a more specific match wins when
    one exists. Falls back to the simple lookup if standards_context is absent
    or resolution finds nothing, so pre-Phase-5 callers are unaffected."""
    if standards_context:
        try:
            from ..graphrag.standards import resolve_standards
            result = resolve_standards(
                clause_type=clause_type, document_type=standards_context.get("document_type"),
                org_profile_id=org_profile_id, business_unit_id=standards_context.get("business_unit_id"),
                jurisdiction_id=standards_context.get("jurisdiction_id"), customer_id=standards_context.get("customer_id"),
            )
            approved = [s for s in result["selected"] if s["source_kind"] == "approved_clause"]
            if approved:
                return approved[0]["reference_text"]
        except Exception:
            pass  # fall through to the simple lookup below

    refs = find_knowledge_references(clause_type=clause_type, org_profile_id=org_profile_id, approval_status="approved")
    refs = [r for r in refs if r["source_kind"] == "approved_clause"]
    return refs[0]["reference_text"] if refs else None


def resolve_business_unit_id(org_profile_id: Optional[str], business_unit_name: Optional[str]) -> Optional[str]:
    """Looks up a business_unit row by name or code, scoped to org_profile_id
    (a document's free-text business_unit field is matched against this, since
    Document has no FK to business_unit -- see models.py comment on Document.business_unit)."""
    if not org_profile_id or not business_unit_name:
        return None
    from .models import BusinessUnit
    with get_session() as session:
        row = session.execute(
            select(BusinessUnit).where(
                BusinessUnit.org_profile_id == org_profile_id,
                (BusinessUnit.name == business_unit_name) | (BusinessUnit.code == business_unit_name),
            )
        ).scalars().first()
        return row.business_unit_id if row else None


def resolve_jurisdiction_id(jurisdiction_name_or_code: Optional[str]) -> Optional[str]:
    """Looks up a jurisdiction row by name or code (a document's free-text geography
    field is matched against this, since Document has no FK to jurisdiction)."""
    if not jurisdiction_name_or_code:
        return None
    from .models import Jurisdiction
    with get_session() as session:
        row = session.execute(
            select(Jurisdiction).where(
                (Jurisdiction.name == jurisdiction_name_or_code) | (Jurisdiction.code == jurisdiction_name_or_code.upper())
            )
        ).scalars().first()
        return row.jurisdiction_id if row else None


def get_org_profile_customer_id(org_profile_id: Optional[str]) -> Optional[str]:
    if not org_profile_id:
        return None
    with get_session() as session:
        profile = session.get(OrgProfile, org_profile_id)
        return profile.customer_id if profile else None


def find_standards_candidates(clause_type: Optional[str], document_type: Optional[str],
                               org_profile_id: Optional[str], business_unit_id: Optional[str],
                               jurisdiction_id: Optional[str], customer_id: Optional[str],
                               include_unapproved: bool = False) -> list[dict]:
    """Pulls every KnowledgeReference row that COULD apply to this context --
    matching clause_type exactly, and matching org_profile_id/business_unit_id/
    jurisdiction_id/document_type/customer_id only where the row itself sets that
    scope (a null scope column on the row means "not restricted to a specific X",
    not "excluded"). Never returns rows for a different customer_id -- section
    11.3's tenant-isolation rule. The resolve_standards() service in
    graphrag/standards.py is what actually ranks/selects from this candidate set;
    this function's job is just a safe, tenant-scoped SQL filter."""
    with get_session() as session:
        stmt = select(KnowledgeReference)
        if clause_type:
            stmt = stmt.where(KnowledgeReference.clause_type == clause_type)
        if not include_unapproved:
            stmt = stmt.where(KnowledgeReference.approval_status == "approved")

        # tenant isolation: a row scoped to a customer_id may only be seen by that
        # customer; a row with no customer_id at all is treated as a customer/group
        # fallback (hierarchy level 8) visible to everyone -- never cross a real
        # customer_id boundary.
        if customer_id:
            stmt = stmt.where((KnowledgeReference.customer_id == customer_id) | (KnowledgeReference.customer_id.is_(None)))
        else:
            stmt = stmt.where(KnowledgeReference.customer_id.is_(None))

        rows = session.execute(stmt).scalars().all()

        def _scope_compatible(row: KnowledgeReference) -> bool:
            if row.org_profile_id and row.org_profile_id != org_profile_id:
                return False
            if row.business_unit_id and row.business_unit_id != business_unit_id:
                return False
            if row.jurisdiction_id and row.jurisdiction_id != jurisdiction_id:
                return False
            if row.document_type and document_type and row.document_type != document_type:
                return False
            if row.document_type and not document_type:
                return False
            return True

        compatible = [r for r in rows if _scope_compatible(r)]

        return [
            {
                "knowledge_reference_id": r.knowledge_reference_id,
                "clause_type": r.clause_type,
                "title": r.title,
                "reference_text": r.reference_text,
                "source_kind": r.source_kind,
                "approval_status": r.approval_status,
                "is_mandatory": r.is_mandatory,
                "is_prohibited": r.is_prohibited,
                "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                "expiry_date": r.expiry_date.isoformat() if r.expiry_date else None,
                "version": r.version,
                "customer_id": r.customer_id,
                "org_profile_id": r.org_profile_id,
                "business_unit_id": r.business_unit_id,
                "jurisdiction_id": r.jurisdiction_id,
                "document_type": r.document_type,
                "business_unit_scope": r.business_unit_scope,
                "jurisdiction_scope": r.jurisdiction_scope,
                "extra": r.extra,
            }
            for r in compatible
        ]


def _approval_step_dict(s: "ApprovalStep") -> dict:
    return {
        "approval_step_id": s.approval_step_id, "sequence": s.sequence, "required_role": s.required_role,
        "reason": s.reason, "status": s.status, "decided_by": s.decided_by,
        "decided_at": s.decided_at.isoformat() if s.decided_at else None,
        "comments": s.comments, "required_changes": s.required_changes, "send_back_reason": s.send_back_reason,
        "created_at": s.created_at.isoformat(),
    }


def _decision_brief_dict(b: "DecisionBrief", steps: list[dict]) -> dict:
    return {
        "decision_brief_id": b.decision_brief_id, "document_id": b.document_id,
        "version_number": b.version_number, "generated_by": b.generated_by, "sections": b.sections,
        "recommendation": b.recommendation, "evidence_validated": b.evidence_validated,
        "unsupported_statements": b.unsupported_statements or [], "status": b.status,
        "superseded_by_version": b.superseded_by_version, "created_at": b.created_at.isoformat(),
        "approval_steps": steps,
    }


def create_decision_brief(document_id: str, generated_by: str, sections: dict, recommendation: str,
                           evidence_validated: bool, unsupported_statements: list[str],
                           approval_chain: list[dict]) -> dict:
    """Always creates a new version (append-only, same convention as
    SummaryVersion) -- a send-back on an earlier version marks it superseded
    rather than deleting or mutating it (section 15.4: correction requires a
    new event/version, not deletion)."""
    with get_session() as session:
        last = session.execute(
            select(DecisionBrief)
            .where(DecisionBrief.document_id == document_id)
            .order_by(DecisionBrief.version_number.desc())
        ).scalars().first()
        next_version = (last.version_number + 1) if last else 1

        brief = DecisionBrief(
            document_id=document_id, version_number=next_version, generated_by=generated_by,
            sections=sections, recommendation=recommendation, evidence_validated=evidence_validated,
            unsupported_statements=unsupported_statements,
        )
        session.add(brief)
        session.flush()

        if last is not None and last.status == "sent_back":
            last.superseded_by_version = next_version

        steps = []
        for i, step in enumerate(approval_chain):
            row = ApprovalStep(
                decision_brief_id=brief.decision_brief_id, sequence=i,
                required_role=step["required_role"], reason=step.get("reason"),
            )
            session.add(row)
            steps.append(row)
        session.flush()

        return _decision_brief_dict(brief, [_approval_step_dict(s) for s in steps])


def get_latest_decision_brief(document_id: str) -> Optional[dict]:
    with get_session() as session:
        brief = session.execute(
            select(DecisionBrief)
            .where(DecisionBrief.document_id == document_id)
            .order_by(DecisionBrief.version_number.desc())
        ).scalars().first()
        if brief is None:
            return None
        steps = session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.decision_brief_id == brief.decision_brief_id)
            .order_by(ApprovalStep.sequence.asc())
        ).scalars().all()
        return _decision_brief_dict(brief, [_approval_step_dict(s) for s in steps])


def get_decision_brief_history(document_id: str) -> list[dict]:
    with get_session() as session:
        briefs = session.execute(
            select(DecisionBrief)
            .where(DecisionBrief.document_id == document_id)
            .order_by(DecisionBrief.version_number.asc())
        ).scalars().all()
        result = []
        for brief in briefs:
            steps = session.execute(
                select(ApprovalStep)
                .where(ApprovalStep.decision_brief_id == brief.decision_brief_id)
                .order_by(ApprovalStep.sequence.asc())
            ).scalars().all()
            result.append(_decision_brief_dict(brief, [_approval_step_dict(s) for s in steps]))
        return result


def get_decision_brief(decision_brief_id: str) -> Optional[dict]:
    with get_session() as session:
        brief = session.get(DecisionBrief, decision_brief_id)
        if brief is None:
            return None
        steps = session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.decision_brief_id == brief.decision_brief_id)
            .order_by(ApprovalStep.sequence.asc())
        ).scalars().all()
        return _decision_brief_dict(brief, [_approval_step_dict(s) for s in steps])


def get_next_pending_approval_step(decision_brief_id: str) -> Optional[dict]:
    """The current assigned approver per 15.4's 'only the current assigned
    approver ... can decide' -- the first step in sequence order that's still
    pending. Once every step is decided, returns None."""
    with get_session() as session:
        step = session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.decision_brief_id == decision_brief_id, ApprovalStep.status == "pending")
            .order_by(ApprovalStep.sequence.asc())
        ).scalars().first()
        return _approval_step_dict(step) if step else None


def decide_approval_step(approval_step_id: str, decided_by: str, decision: str,
                          comments: Optional[str] = None, required_changes: Optional[str] = None,
                          send_back_reason: Optional[str] = None) -> dict:
    """Applies one approval decision (approve/approve_with_changes/reject/send_back).
    Section 15.4 rules (comments required for reject, required_changes required for
    approve_with_changes, reason required for send_back) are enforced by the caller
    (the API layer, consistent with how confidentiality override validation is done
    at the API/schema layer, not buried in the repository) -- this function trusts
    its inputs and just persists the decision as an immutable event. If this step
    is the last one and the decision is a terminal approval, the parent
    DecisionBrief's status is updated to match; a send_back marks the brief
    'sent_back' so a new review cycle/version is expected next."""
    from datetime import datetime, timezone

    with get_session() as session:
        step = session.get(ApprovalStep, approval_step_id)
        if step is None:
            raise ValueError(f"No approval step found for approval_step_id={approval_step_id}")
        if step.status != "pending":
            raise ValueError(f"Approval step {approval_step_id} is not pending (status={step.status})")

        step.status = decision
        step.decided_by = decided_by
        step.decided_at = datetime.now(timezone.utc)
        step.comments = comments
        step.required_changes = required_changes
        step.send_back_reason = send_back_reason
        session.flush()

        brief = session.get(DecisionBrief, step.decision_brief_id)
        if decision in ("reject", "send_back"):
            brief.status = decision
            # a reject/send-back stops the chain -- remaining steps never become
            # "current" (section 15.4 implies later approvers only ever see a
            # brief that reached them, not one already killed earlier in the chain)
            still_pending = session.execute(
                select(ApprovalStep).where(
                    ApprovalStep.decision_brief_id == step.decision_brief_id,
                    ApprovalStep.status == "pending",
                )
            ).scalars().all()
            for other in still_pending:
                other.status = "skipped"
        else:
            remaining = session.execute(
                select(ApprovalStep).where(
                    ApprovalStep.decision_brief_id == step.decision_brief_id,
                    ApprovalStep.status == "pending",
                )
            ).scalars().first()
            if remaining is None:
                brief.status = decision  # last step decided: brief takes on its final decision
        session.flush()

        return _approval_step_dict(step)


def _chat_message_dict(m: "ChatMessage") -> dict:
    return {
        "chat_message_id": m.chat_message_id, "document_id": m.document_id, "query_job_id": m.query_job_id,
        "question": m.question, "answer": m.answer, "citations": m.citations or [], "status": m.status,
        "asked_by": m.asked_by, "route": m.route, "created_at": m.created_at.isoformat(),
    }


def record_chat_message(document_id: str, question: str, answer: Optional[str], status: str,
                         citations: Optional[list] = None, asked_by: Optional[str] = None,
                         query_job_id: Optional[str] = None, retrieved_contexts: Optional[list] = None,
                         route: Optional[str] = None) -> dict:
    """Appends one chat turn. Called once the query graph reaches a terminal
    state (answered/rejected/escalated/evidence_rejected/evidence_escalated) --
    not on every intermediate pause, so a turn that's still mid-review isn't
    yet part of the transcript or fed back in as memory."""
    with get_session() as session:
        row = ChatMessage(
            document_id=document_id, query_job_id=query_job_id, question=question, answer=answer,
            citations=citations or [], status=status, asked_by=asked_by,
            retrieved_contexts=retrieved_contexts or [], route=route,
        )
        session.add(row)
        session.flush()
        return _chat_message_dict(row)


def get_chat_history(document_id: str, include_cleared: bool = False) -> list[dict]:
    """Ascending by created_at (oldest first, i.e. conversation order). By default
    only returns turns after the document's chat_cleared_at marker (section 17.5's
    'clear chat' action) -- pass include_cleared=True for a full-transcript view
    (e.g. an audit/history screen) rather than what should be fed back in as
    conversational memory."""
    with get_session() as session:
        doc = session.get(Document, document_id)
        cleared_at = None if include_cleared or doc is None else doc.chat_cleared_at

        stmt = select(ChatMessage).where(ChatMessage.document_id == document_id)
        if cleared_at is not None:
            stmt = stmt.where(ChatMessage.created_at > cleared_at)
        stmt = stmt.order_by(ChatMessage.created_at.asc())

        rows = session.execute(stmt).scalars().all()
        return [_chat_message_dict(r) for r in rows]


def clear_chat_history(document_id: str) -> dict:
    """Sets Document.chat_cleared_at to now -- a soft clear (section 17.5's
    'optional clear-chat action'). Existing ChatMessage rows are never deleted,
    consistent with every other history table in this codebase; get_chat_history()
    with its default include_cleared=False simply stops surfacing anything at or
    before this marker as active conversational memory."""
    from datetime import datetime, timezone

    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"No document found for document_id={document_id}")
        doc.chat_cleared_at = datetime.now(timezone.utc)
        session.flush()
        return {"document_id": document_id, "chat_cleared_at": doc.chat_cleared_at.isoformat()}


def _evaluation_case_result_dict(c: "EvaluationCaseResult") -> dict:
    return {
        "evaluation_case_result_id": c.evaluation_case_result_id, "domain": c.domain, "case_label": c.case_label,
        "passed": c.passed, "scores": c.scores or {}, "detail": c.detail or {}, "created_at": c.created_at.isoformat(),
    }


def _evaluation_run_dict(r: "EvaluationRun", case_results: list[dict]) -> dict:
    return {
        "evaluation_run_id": r.evaluation_run_id, "triggered_by": r.triggered_by, "commit_sha": r.commit_sha,
        "gemini_model": r.gemini_model, "embedding_model": r.embedding_model,
        "dataset_versions": r.dataset_versions or {}, "metrics": r.metrics, "case_counts": r.case_counts or {},
        "status": r.status, "error_detail": r.error_detail, "duration_seconds": r.duration_seconds,
        "started_at": r.started_at.isoformat(),
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "case_results": case_results,
    }


def create_evaluation_run(triggered_by: Optional[str], commit_sha: Optional[str], gemini_model: Optional[str],
                           embedding_model: Optional[str], dataset_versions: dict, metrics: dict,
                           case_counts: dict, duration_seconds: float, case_results: list[dict],
                           status: str = "completed", error_detail: Optional[str] = None) -> dict:
    """Persists one evaluation run and its per-case breakdown. Always a fresh
    row -- append-only, like DecisionBrief/ChatMessage/AuditLog. case_results is
    a list of {"domain","case_label","passed","scores","detail"} dicts."""
    from datetime import datetime, timezone

    with get_session() as session:
        run = EvaluationRun(
            triggered_by=triggered_by, commit_sha=commit_sha, gemini_model=gemini_model,
            embedding_model=embedding_model, dataset_versions=dataset_versions, metrics=metrics,
            case_counts=case_counts, status=status, error_detail=error_detail, duration_seconds=duration_seconds,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.flush()

        rows = []
        for c in case_results:
            row = EvaluationCaseResult(
                evaluation_run_id=run.evaluation_run_id, domain=c["domain"], case_label=c["case_label"],
                passed=c.get("passed"), scores=c.get("scores") or {}, detail=c.get("detail") or {},
            )
            session.add(row)
            rows.append(row)
        session.flush()

        return _evaluation_run_dict(run, [_evaluation_case_result_dict(r) for r in rows])


def get_latest_evaluation_run() -> Optional[dict]:
    with get_session() as session:
        run = session.execute(
            select(EvaluationRun).order_by(EvaluationRun.started_at.desc())
        ).scalars().first()
        if run is None:
            return None
        case_results = session.execute(
            select(EvaluationCaseResult).where(EvaluationCaseResult.evaluation_run_id == run.evaluation_run_id)
        ).scalars().all()
        return _evaluation_run_dict(run, [_evaluation_case_result_dict(c) for c in case_results])


def get_evaluation_run(evaluation_run_id: str) -> Optional[dict]:
    with get_session() as session:
        run = session.get(EvaluationRun, evaluation_run_id)
        if run is None:
            return None
        case_results = session.execute(
            select(EvaluationCaseResult).where(EvaluationCaseResult.evaluation_run_id == run.evaluation_run_id)
        ).scalars().all()
        return _evaluation_run_dict(run, [_evaluation_case_result_dict(c) for c in case_results])


def get_evaluation_run_history(limit: int = 20) -> list[dict]:
    """Descending by started_at (most recent first) -- section 28.2's 'evaluation
    run trend over time' chart reads this without needing per-case detail, so
    case_results are omitted here for size; fetch a single run via
    get_evaluation_run() for its breakdown."""
    with get_session() as session:
        runs = session.execute(
            select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
        ).scalars().all()
        return [_evaluation_run_dict(r, []) for r in runs]
