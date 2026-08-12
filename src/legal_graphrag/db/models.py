"""SQLAlchemy ORM models for the operational data store.

Neo4j still owns clause/document graph relationships (SAME_CLAUSE_AS,
CONFLICTS_WITH, INTERPRETED_BY). These tables own everything that used to be
flat JSON/CSV files plus the new signature-feature records: org profile
config, document/clause/risk-flag rows, review actions, the append-only audit
log, editable summary history, and the approved-clause/policy knowledge
library used by RAG retrieval filtering.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    """Top-level tenant boundary. Single row (Tata Group) today; the schema
    stays SaaS-ready for additional customers without any code path assuming
    there's only one."""
    __tablename__ = "customer"

    customer_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subscription_tier: Mapped[str] = mapped_column(String(50), default="standard")
    logo_url: Mapped[str] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[str] = mapped_column(String(20), nullable=True)
    app_name: Mapped[str] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    org_profiles: Mapped[list["OrgProfile"]] = relationship(back_populates="customer")


class Jurisdiction(Base):
    __tablename__ = "jurisdiction"

    jurisdiction_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_system: Mapped[str] = mapped_column(String(100), nullable=True)
    privacy_regime: Mapped[str] = mapped_column(String(200), nullable=True)
    data_localization_required: Mapped[bool] = mapped_column(Boolean, default=False)
    compliance_notes: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class OrgProfile(Base):
    """Represents a Tata company (the blueprint's "Organization"). Kept as the
    existing org_profile table/PK so the 15+ existing call sites resolving
    checklists/risk overrides/knowledge references by org_profile_id keep
    working unchanged; customer_id and the new descriptive fields are additive."""
    __tablename__ = "org_profile"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer.customer_id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry_sector: Mapped[str] = mapped_column(String(100), nullable=True)
    default_risk_tolerance: Mapped[str] = mapped_column(String(50), nullable=True)
    jurisdiction_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    required_clause_checklist: Mapped[dict] = mapped_column(JSON, default=dict)  # keyed by contract_type
    risk_threshold_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    confidentiality_default: Mapped[str] = mapped_column(String(50), default="internal")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    customer: Mapped["Customer"] = relationship(back_populates="org_profiles")
    business_units: Mapped[list["BusinessUnit"]] = relationship(back_populates="org_profile")
    documents: Mapped[list["Document"]] = relationship(back_populates="org_profile")
    knowledge_references: Mapped[list["KnowledgeReference"]] = relationship(back_populates="org_profile")


class BusinessUnit(Base):
    """Sub-scope within an OrgProfile/Organization, e.g. "TCS North America".
    No rows exist yet -- this is schema-only until Phase 5 seeds real data."""
    __tablename__ = "business_unit"

    business_unit_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer.customer_id"), nullable=True)
    org_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("org_profile.profile_id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    geography: Mapped[str] = mapped_column(String(100), nullable=True)
    industry_vertical: Mapped[str] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    org_profile: Mapped["OrgProfile"] = relationship(back_populates="business_units")


class Document(Base):
    __tablename__ = "document"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=True)  # MinIO object key
    collection_name: Mapped[str] = mapped_column(String(500), nullable=True)  # Chroma collection this document's chunks live in
    document_type: Mapped[str] = mapped_column(String(100), nullable=True)  # contract_type
    business_unit: Mapped[str] = mapped_column(String(200), nullable=True)  # free-text name/code, resolved against business_unit.name/code for standards lookup
    counterparty: Mapped[str] = mapped_column(String(200), nullable=True)
    geography: Mapped[str] = mapped_column(String(100), nullable=True)  # free-text name/code, resolved against jurisdiction.name/code for standards lookup
    confidentiality_level: Mapped[str] = mapped_column(String(50), nullable=True)
    confidentiality_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    confidentiality_source: Mapped[str] = mapped_column(String(20), nullable=True)  # automatic/manual_override/default
    confidentiality_reasons: Mapped[list] = mapped_column(JSON, nullable=True)  # [{"reason","page","evidence"}, ...]
    confidentiality_needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)  # deterministic/LLM disagreed by >1 level
    review_priority: Mapped[str] = mapped_column(String(50), nullable=True)  # low/medium/high/urgent
    org_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("org_profile.profile_id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(50), default="uploaded")
    # links to the Neo4j job_id/contract_id created for this document, so the two
    # stores can be joined without duplicating clause/graph data into Postgres
    job_id: Mapped[str] = mapped_column(String(36), nullable=True)
    contract_id: Mapped[str] = mapped_column(String(36), nullable=True)
    # contract metadata (from contract_metadata.extract_contract_metadata()), persisted
    # here so it survives past the ingestion job response -- previously only lived in
    # transient IngestionState/document_context, unavailable to anything reading the
    # document later (Decision Brief's key dates/financial terms need this, section 15.3)
    parties: Mapped[list] = mapped_column(JSON, nullable=True)  # [{"name","role"}, ...]
    subject_matter: Mapped[str] = mapped_column(Text, nullable=True)
    effective_date: Mapped[str] = mapped_column(String(20), nullable=True)  # ISO 8601 date
    end_date: Mapped[str] = mapped_column(String(20), nullable=True)  # ISO 8601 date
    monetary_value: Mapped[float] = mapped_column(Float, nullable=True)
    governing_law_country: Mapped[str] = mapped_column(String(10), nullable=True)
    # section 17.5's "optional clear-chat action": a soft marker, not a delete --
    # ChatMessage rows are append-only like everything else here, so "clearing"
    # means "stop feeding rows at/before this timestamp back in as memory," not
    # destroying the transcript (chat history is still audit-relevant, section 17.5's
    # "chat history access controlled by document permissions" implies it survives).
    chat_cleared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    org_profile: Mapped["OrgProfile"] = relationship(back_populates="documents")
    clauses: Mapped[list["Clause"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    summary_versions: Mapped[list["SummaryVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    confidentiality_overrides: Mapped[list["ConfidentialityOverride"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    decision_briefs: Mapped[list["DecisionBrief"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Clause(Base):
    __tablename__ = "clause"

    clause_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    clause_type: Mapped[str] = mapped_column(String(100), nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_reference: Mapped[str] = mapped_column(String(50), nullable=True)
    party: Mapped[str] = mapped_column(String(300), nullable=True)
    obligation_owner: Mapped[str] = mapped_column(String(300), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # dedup key
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="clauses")
    risk_flags: Mapped[list["RiskFlag"]] = relationship(back_populates="clause", cascade="all, delete-orphan")


class RiskFlag(Base):
    """clause_id is nullable because some risk categories are document-level, not
    tied to one extracted clause (missing_clause: the clause doesn't exist to link
    to; compliance_gap/unusual_governing_law: evaluated against document-level
    metadata). document_id is set for those; clause-level flags leave it null and
    rely on clause.document_id instead (avoids a redundant denormalized column for
    the common case)."""
    __tablename__ = "risk_flag"

    risk_flag_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clause_id: Mapped[str] = mapped_column(String(36), ForeignKey("clause.clause_id"), nullable=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=True)  # section 13.4: missing_clause/non_standard/ambiguous/
    # conflicting_terms/duplicate_clause/prohibited_language/compliance_gap/value_threshold/
    # unusual_governing_law/excessive_liability/auto_renewal/asymmetric_rights
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # low/medium/high
    rationale: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=True)
    deviation_score: Mapped[float] = mapped_column(Float, nullable=True)  # cosine similarity vs approved language
    deviation_detail: Mapped[dict] = mapped_column(JSON, nullable=True)  # structured diff, section 4
    confidence_breakdown: Mapped[dict] = mapped_column(JSON, nullable=True)  # section 7 composite components
    standards_evidence: Mapped[dict] = mapped_column(JSON, nullable=True)  # section 13.4: resolved standard(s) this finding was checked against
    applicable_rule_source: Mapped[str] = mapped_column(String(100), nullable=True)  # e.g. "llm_risk_review", "deterministic:duplicate_clause", knowledge_reference_id
    assigned_role: Mapped[str] = mapped_column(String(50), default="reviewer")  # section 8 routing
    reviewer_status: Mapped[str] = mapped_column(String(50), default="pending")  # pending/accepted/edited/rejected/escalated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    clause: Mapped["Clause"] = relationship(back_populates="risk_flags")
    playbook_entry: Mapped["PlaybookEntry"] = relationship(back_populates="risk_flag", uselist=False, cascade="all, delete-orphan")


class PlaybookEntry(Base):
    __tablename__ = "playbook_entry"

    playbook_entry_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    risk_flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("risk_flag.risk_flag_id"), nullable=False, unique=True)
    current_language: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_positions: Mapped[list] = mapped_column(JSON, default=list)  # ranked ideal -> acceptable
    fallback_source: Mapped[str] = mapped_column(String(50), default="llm_generated")  # "org_profile" or "llm_generated"
    suggested_redline: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    risk_flag: Mapped["RiskFlag"] = relationship(back_populates="playbook_entry")


class ReviewAction(Base):
    __tablename__ = "review_action"

    review_action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=True)
    clause_id: Mapped[str] = mapped_column(String(36), ForeignKey("clause.clause_id"), nullable=True)
    risk_flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("risk_flag.risk_flag_id"), nullable=True)
    reviewer_username: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # accept/edit/reject/escalate/comment
    rationale: Mapped[str] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Append-only canonical history store. Never update or delete a row here."""
    __tablename__ = "audit_log"

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), nullable=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SummaryVersion(Base):
    """Append-only, like AuditLog/ConfidentialityOverride -- every edit or restore
    creates a NEW row, never mutates an existing one (section 14.1: "do not
    overwrite approved summary history" applies to every version, not just
    approved ones, since there's no update path here at all)."""
    __tablename__ = "summary_version"

    summary_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by: Mapped[str] = mapped_column(String(100), nullable=True)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)  # explicit flag (section 14.1); edited_by is None for the initial AI pass, but a human "restore" also has edited_by set while still ultimately AI-authored text
    approval_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/approved
    approved_by: Mapped[str] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_from_version: Mapped[int] = mapped_column(Integer, nullable=True)  # set when this version was created by restoring an earlier one
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="summary_versions")


class ConfidentialityOverride(Base):
    """Append-only history of confidentiality-level changes for a document, both the
    initial automatic classification and every human override thereafter (section 12.3).
    Never update or delete a row here -- Document.confidentiality_level/source always
    reflect the latest row, this table is the "override history" the UI shows."""
    __tablename__ = "confidentiality_override"

    override_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    previous_level: Mapped[str] = mapped_column(String(50), nullable=True)
    new_level: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # automatic/manual_override/default
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=True)  # null for automatic classification
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="confidentiality_overrides")


class KnowledgeReference(Base):
    """Clause-library / policy entries used by RAG retrieval, filterable and rankable
    by approval_status, business_unit_scope, and jurisdiction_scope (guideline 5.4).
    Phase 5 adds document_type/customer_id/business_unit_id/effective/expiry so the
    8-level resolution hierarchy (section 11.1) has real scope levels to match on,
    on top of the pre-existing string-based business_unit_scope/jurisdiction_scope
    (kept for the 57 existing rows seeded before business_unit rows existed)."""
    __tablename__ = "knowledge_reference"

    knowledge_reference_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer.customer_id"), nullable=True)
    org_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("org_profile.profile_id"), nullable=True)
    business_unit_id: Mapped[str] = mapped_column(String(36), ForeignKey("business_unit.business_unit_id"), nullable=True)
    jurisdiction_id: Mapped[str] = mapped_column(String(36), ForeignKey("jurisdiction.jurisdiction_id"), nullable=True)
    document_type: Mapped[str] = mapped_column(String(100), nullable=True)  # contract_type; null = applies to all types
    clause_type: Mapped[str] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=True)
    reference_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(50), default="approved_clause")  # approved_clause / risk_taxonomy / policy
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")  # approved/unapproved/outdated
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=False)  # jurisdiction-mandatory requirements (11.2)
    is_prohibited: Mapped[bool] = mapped_column(Boolean, default=False)  # prohibited-clause entries (11.2)
    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    business_unit_scope: Mapped[str] = mapped_column(String(200), nullable=True)
    jurisdiction_scope: Mapped[str] = mapped_column(String(100), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, nullable=True)  # severity/typical_clause_types/etc for risk-taxonomy rows
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    org_profile: Mapped["OrgProfile"] = relationship(back_populates="knowledge_references")


class DecisionBrief(Base):
    """Section 15.2/15.3: a versioned, structured brief generated when the first
    reviewer completes review. Append-only like SummaryVersion -- a send-back
    starts a new review cycle and a new brief version, never overwrites a prior
    one (section 15.4: correction requires a new event/version, not deletion).
    The 13 required sections (15.3) are stored as one JSON payload rather than
    13 separate columns, since they're generated and displayed together and
    nothing queries into an individual section."""
    __tablename__ = "decision_brief"

    decision_brief_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(100), nullable=False)  # first reviewer who completed review
    sections: Mapped[dict] = mapped_column(JSON, nullable=False)  # the 13 sections from 15.3
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False)  # approve/approve_with_changes/reject/escalate
    evidence_validated: Mapped[bool] = mapped_column(Boolean, default=False)  # 15.2.4: every material statement checked against structured data
    unsupported_statements: Mapped[list] = mapped_column(JSON, nullable=True)  # statements evidence-validation couldn't ground, surfaced not silently dropped
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/approved_with_changes/rejected/sent_back
    superseded_by_version: Mapped[int] = mapped_column(Integer, nullable=True)  # set once a send-back produces a newer version
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="decision_briefs")
    approval_steps: Mapped[list["ApprovalStep"]] = relationship(back_populates="decision_brief", cascade="all, delete-orphan")


class ApprovalStep(Base):
    """One step in a DecisionBrief's approval chain (section 15.5). The chain is
    resolved once at brief-generation time and persisted as an ordered list of
    steps so 'next approver can decide without re-entering document data' (Phase
    8 exit criteria) -- nothing needs to re-resolve the policy on every page load.
    Each step's decision is an immutable audit event (section 15.4): once decided,
    a step is never edited, only read."""
    __tablename__ = "approval_step"

    approval_step_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    decision_brief_id: Mapped[str] = mapped_column(String(36), ForeignKey("decision_brief.decision_brief_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based position in the chain
    required_role: Mapped[str] = mapped_column(String(50), nullable=False)  # reviewer/senior_counsel/business_head/clo/admin
    reason: Mapped[str] = mapped_column(String(200), nullable=True)  # why this step exists, e.g. "high risk severity"
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/approved_with_changes/rejected/sent_back/skipped
    decided_by: Mapped[str] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    comments: Mapped[str] = mapped_column(Text, nullable=True)  # required for reject
    required_changes: Mapped[str] = mapped_column(Text, nullable=True)  # required for approve_with_changes
    send_back_reason: Mapped[str] = mapped_column(Text, nullable=True)  # required for send_back
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    decision_brief: Mapped["DecisionBrief"] = relationship(back_populates="approval_steps")


class ChatMessage(Base):
    """Section 17.5: one turn of the per-document Ask CounselGraph chat. Append-only
    like every other history table here -- a "clear chat" action sets
    Document.chat_cleared_at instead of deleting rows, so the underlying transcript
    stays available for audit while no longer being fed back in as conversational
    memory. asked_by is nullable because the query graph's evidence/answer human
    checkpoints don't currently require a caller identity the way document review
    actions do; it's populated when the caller is known (the API layer has it via
    the session) and left null only for any programmatic/test caller that doesn't."""
    __tablename__ = "chat_message"

    chat_message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    query_job_id: Mapped[str] = mapped_column(String(36), nullable=True)  # Neo4j QueryJob this turn belongs to
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=True)  # null if the turn ended in rejection/escalation, never answered
    citations: Mapped[list] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # answered/rejected/escalated/evidence_rejected/evidence_escalated
    asked_by: Mapped[str] = mapped_column(String(100), nullable=True)
    retrieved_contexts: Mapped[list] = mapped_column(JSON, nullable=True)  # section 24.2 RAGAS record: raw hybrid/graph hits used
    route: Mapped[str] = mapped_column(String(50), nullable=True)  # hybrid/graph/whole_document/direct, for eval breakdowns
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped["Document"] = relationship(back_populates="chat_messages")
