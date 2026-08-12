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

    org_profile: Mapped["OrgProfile"] = relationship(back_populates="documents")
    clauses: Mapped[list["Clause"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    summary_versions: Mapped[list["SummaryVersion"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    confidentiality_overrides: Mapped[list["ConfidentialityOverride"]] = relationship(back_populates="document", cascade="all, delete-orphan")


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
    __tablename__ = "risk_flag"

    risk_flag_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clause_id: Mapped[str] = mapped_column(String(36), ForeignKey("clause.clause_id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # low/medium/high
    rationale: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=True)
    deviation_score: Mapped[float] = mapped_column(Float, nullable=True)  # cosine similarity vs approved language
    deviation_detail: Mapped[dict] = mapped_column(JSON, nullable=True)  # structured diff, section 4
    confidence_breakdown: Mapped[dict] = mapped_column(JSON, nullable=True)  # section 7 composite components
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
    __tablename__ = "summary_version"

    summary_version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("document.document_id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by: Mapped[str] = mapped_column(String(100), nullable=True)
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
