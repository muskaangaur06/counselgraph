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


class OrgProfile(Base):
    __tablename__ = "org_profile"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    jurisdiction_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    required_clause_checklist: Mapped[dict] = mapped_column(JSON, default=dict)  # keyed by contract_type
    risk_threshold_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    confidentiality_default: Mapped[str] = mapped_column(String(50), default="internal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="org_profile")
    knowledge_references: Mapped[list["KnowledgeReference"]] = relationship(back_populates="org_profile")


class Document(Base):
    __tablename__ = "document"

    document_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=True)  # MinIO object key
    document_type: Mapped[str] = mapped_column(String(100), nullable=True)  # contract_type
    business_unit: Mapped[str] = mapped_column(String(200), nullable=True)
    counterparty: Mapped[str] = mapped_column(String(200), nullable=True)
    geography: Mapped[str] = mapped_column(String(100), nullable=True)
    confidentiality_level: Mapped[str] = mapped_column(String(50), nullable=True)
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


class KnowledgeReference(Base):
    """Clause-library / policy entries used by RAG retrieval, filterable and rankable
    by approval_status, business_unit_scope, and jurisdiction_scope (guideline 5.4)."""
    __tablename__ = "knowledge_reference"

    knowledge_reference_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("org_profile.profile_id"), nullable=True)
    clause_type: Mapped[str] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=True)
    reference_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(50), default="approved_clause")  # approved_clause / risk_taxonomy / policy
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")  # approved/unapproved/outdated
    version: Mapped[int] = mapped_column(Integer, default=1)
    business_unit_scope: Mapped[str] = mapped_column(String(200), nullable=True)
    jurisdiction_scope: Mapped[str] = mapped_column(String(100), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, nullable=True)  # severity/typical_clause_types/etc for risk-taxonomy rows
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    org_profile: Mapped["OrgProfile"] = relationship(back_populates="knowledge_references")
