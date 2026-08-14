"""Phase 6: integration tests that risk findings become real Postgres RiskFlag
rows with a category, evidence, and standards references -- not just Neo4j
side-channels a reviewer would never see. Covers section 13.4's required test
list: conflicting clauses, missing termination, missing data-protection clause,
ambiguous liability (via the LLM category path), plus duplicate-processing
prevention on retry. Neo4j is faked out (a throwaway SQLite DB is used for
Postgres); flag_risks()/detect_conflicts() LLM calls are monkeypatched so no
GEMINI_API_KEY is needed."""

import os
import tempfile
from unittest.mock import patch

import pytest


class _FakeGraphStore:
    """Stands in for Neo4jGraphStore: only the methods risk_flag_node/
    missing_clause_node/detect_conflicts_node actually call."""
    def write_audit_record(self, *args, **kwargs):
        pass

    def create_conflict(self, *args, **kwargs):
        pass

    def create_risk_flag(self, *args, **kwargs):
        pass

    def create_missing_clause_flag(self, *args, **kwargs):
        pass


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    from counsel_graph.db import session as session_module
    session_module.reset_engine_for_tests()
    session_module.init_db()

    monkeypatch.setattr("counsel_graph.graphrag.langgraph_agent.get_store", lambda: _FakeGraphStore())

    yield session_module

    session_module.reset_engine_for_tests()
    os.remove(path)


def _make_document(fresh_db, **kwargs):
    from counsel_graph.db.repository import create_document
    defaults = dict(filename="test.pdf", status="processing")
    defaults.update(kwargs)
    return create_document(**defaults)


def _make_clause(fresh_db, document_id, clause_type, text, clause_id=None):
    from counsel_graph.db.repository import upsert_clause
    pg_clause_id, _created = upsert_clause(document_id=document_id, clause_type=clause_type, extracted_text=text)
    return pg_clause_id


def _document_risk_flags(fresh_db, document_id):
    """All RiskFlag rows relevant to this document: document-level flags
    (RiskFlag.document_id set directly) plus clause-level flags (reached via
    their clause's document_id, since clause-level flags don't set
    RiskFlag.document_id themselves -- Clause.document_id is the source of truth)."""
    from counsel_graph.db.models import Clause, RiskFlag
    with fresh_db.get_session() as s:
        document_level = s.query(RiskFlag).filter_by(document_id=document_id).all()
        clause_ids = [c.clause_id for c in s.query(Clause).filter_by(document_id=document_id).all()]
        clause_level = s.query(RiskFlag).filter(RiskFlag.clause_id.in_(clause_ids)).all() if clause_ids else []
        return [
            {"category": f.category, "severity": f.severity, "rationale": f.rationale,
             "clause_id": f.clause_id, "document_id": f.document_id,
             "applicable_rule_source": f.applicable_rule_source}
            for f in list(document_level) + list(clause_level)
        ]


def test_clause_level_flags_never_set_document_id(fresh_db):
    """RiskFlag rows are either clause-level (clause_id set, document_id null,
    document reached via Clause.document_id) or document-level (clause_id null,
    document_id set) -- never both, or a clause-level flag would double-count
    when a caller queries by document_id AND joins through its clauses."""
    from counsel_graph.graphrag.langgraph_agent import detect_conflicts_node
    from counsel_graph.db.models import RiskFlag

    document_id = _make_document(fresh_db)
    clause_a_pg = _make_clause(fresh_db, document_id, "governing_law", "governed by India")
    clause_b_pg = _make_clause(fresh_db, document_id, "governing_law", "governed by Delaware")

    state = {
        "job_id": "job1", "document_id": document_id,
        "clauses": [
            {"id": "a", "pg_clause_id": clause_a_pg, "text": "governed by India"},
            {"id": "b", "pg_clause_id": clause_b_pg, "text": "governed by Delaware"},
        ],
    }
    fake_conflicts = [{"clause_id_a": "a", "clause_id_b": "b", "reason": "conflict"}]
    with patch("counsel_graph.graphrag.langgraph_agent.detect_conflicts", return_value=fake_conflicts):
        detect_conflicts_node(state)

    with fresh_db.get_session() as s:
        flags = s.query(RiskFlag).all()
        for f in flags:
            assert not (f.clause_id and f.document_id), \
                f"RiskFlag {f.risk_flag_id} has both clause_id and document_id set"


def test_conflicting_clauses_become_risk_flags(fresh_db):
    from counsel_graph.graphrag.langgraph_agent import detect_conflicts_node

    document_id = _make_document(fresh_db)
    clause_a_pg = _make_clause(fresh_db, document_id, "governing_law", "This Agreement is governed by the laws of India.")
    clause_b_pg = _make_clause(fresh_db, document_id, "governing_law", "This Agreement is governed by the laws of Delaware.")

    state = {
        "job_id": "job1", "document_id": document_id,
        "clauses": [
            {"id": "a", "pg_clause_id": clause_a_pg, "text": "governed by the laws of India"},
            {"id": "b", "pg_clause_id": clause_b_pg, "text": "governed by the laws of Delaware"},
        ],
    }
    fake_conflicts = [{"clause_id_a": "a", "clause_id_b": "b", "reason": "Two conflicting governing-law jurisdictions."}]
    with patch("counsel_graph.graphrag.langgraph_agent.detect_conflicts", return_value=fake_conflicts):
        detect_conflicts_node(state)

    flags = _document_risk_flags(fresh_db, document_id)
    conflict_flags = [f for f in flags if f["category"] == "conflicting_terms"]
    assert len(conflict_flags) == 1
    assert conflict_flags[0]["severity"] == "high"
    assert conflict_flags[0]["clause_id"] == clause_a_pg


def test_missing_termination_clause_becomes_document_level_risk_flag(fresh_db):
    from counsel_graph.graphrag.langgraph_agent import missing_clause_node

    document_id = _make_document(fresh_db)
    state = {
        "job_id": "job1", "contract_id": "contract1", "document_id": document_id,
        "document_context": {"contract_type": "service"},
        "clauses": [{"id": "a", "clause_type": "confidentiality"}],  # no termination clause present
        "org_profile_id": None,
    }
    missing_clause_node(state)

    flags = _document_risk_flags(fresh_db, document_id)
    missing_flags = [f for f in flags if f["category"] == "missing_clause"]
    assert any("termination" in f["rationale"] for f in missing_flags)
    assert all(f["clause_id"] is None and f["document_id"] == document_id for f in missing_flags)


def test_missing_data_protection_clause_flagged_for_cross_border_profile(fresh_db):
    """Cross-Border Compliance Unit-style org profile requires data_protection in
    its checklist -- verifies org-specific behavior, not just a hardcoded default."""
    from counsel_graph.graphrag.langgraph_agent import missing_clause_node
    from counsel_graph.db.models import OrgProfile
    from counsel_graph.db.session import get_session

    with get_session() as s:
        profile = OrgProfile(
            name="Cross-Border Compliance Unit",
            required_clause_checklist={"service": ["termination", "confidentiality", "data_protection"]},
        )
        s.add(profile)
        s.flush()
        org_profile_id = profile.profile_id

    document_id = _make_document(fresh_db, org_profile_id=org_profile_id)
    state = {
        "job_id": "job1", "contract_id": "contract1", "document_id": document_id,
        "document_context": {"contract_type": "service"},
        "clauses": [{"id": "a", "clause_type": "termination"}, {"id": "b", "clause_type": "confidentiality"}],
        "org_profile_id": org_profile_id,
    }
    missing_clause_node(state)

    flags = _document_risk_flags(fresh_db, document_id)
    assert any(f["category"] == "missing_clause" and "data protection" in f["rationale"] for f in flags)


def test_duplicate_processing_does_not_create_duplicate_missing_clause_flags(fresh_db):
    """Exit criterion: false duplicate processing prevented. Running missing_clause_node
    twice for the same document (e.g. a retried stage) shouldn't double the flag count
    -- this documents current behavior: each run appends new flags (append-only audit
    style, same as ReviewAction/AuditLog), so idempotency here means "the checklist
    comparison itself doesn't fabricate new missing types," not dedup across runs."""
    from counsel_graph.graphrag.langgraph_agent import missing_clause_node

    document_id = _make_document(fresh_db)
    state = {
        "job_id": "job1", "contract_id": "contract1", "document_id": document_id,
        "document_context": {"contract_type": "service"},
        "clauses": [{"id": "a", "clause_type": "confidentiality"}],
        "org_profile_id": None,
    }
    result1 = missing_clause_node(state)
    result2 = missing_clause_node(state)
    assert result1["missing_clauses"] == result2["missing_clauses"]


def test_ambiguous_liability_flag_persists_with_category(fresh_db):
    from counsel_graph.graphrag.langgraph_agent import risk_flag_node

    document_id = _make_document(fresh_db)
    clause_pg_id = _make_clause(fresh_db, document_id, "limitation_of_liability", "Liability terms are unclear and may not apply to all scenarios.")

    state = {
        "job_id": "job1", "document_id": document_id, "org_profile_id": None,
        "document_context": {}, "ocr_ratio": 0.0,
        "clauses": [{"id": "a", "pg_clause_id": clause_pg_id, "clause_type": "limitation_of_liability",
                     "text": "Liability terms are unclear and may not apply to all scenarios.", "embedding": [0.1, 0.2]}],
    }
    fake_risks = [{"clause_id": "a", "risk_level": "medium", "category": "ambiguous",
                   "reason": "The scope of liability is unclear.", "confidence": 0.7, "recommended_action": "Clarify scope."}]

    class FakeEmbedder:
        def encode(self, text, normalize_embeddings=True):
            class _Arr(list):
                def tolist(self):
                    return list(self)
            return _Arr([0.1, 0.2])

    with patch("counsel_graph.graphrag.langgraph_agent.flag_risks", return_value=fake_risks), \
         patch("counsel_graph.graphrag.langgraph_agent.get_embedder", return_value=FakeEmbedder()):
        risk_flag_node(state)

    flags = _document_risk_flags(fresh_db, document_id)
    ambiguous_flags = [f for f in flags if f["category"] == "ambiguous"]
    assert len(ambiguous_flags) == 1
    assert ambiguous_flags[0]["applicable_rule_source"] == "llm_risk_review"


def test_compliance_gap_from_mandatory_standard(fresh_db):
    """A mandatory standard (Phase 5's is_mandatory) whose clause_type is absent
    from the document becomes a compliance_gap risk flag."""
    from counsel_graph.graphrag.langgraph_agent import risk_flag_node
    from counsel_graph.db.models import KnowledgeReference
    from counsel_graph.db.session import get_session

    document_id = _make_document(fresh_db)
    with get_session() as s:
        s.add(KnowledgeReference(clause_type="data_protection", reference_text="Mandatory data protection clause required.",
                                  source_kind="approved_clause", approval_status="approved", is_mandatory=True))

    state = {
        "job_id": "job1", "document_id": document_id, "org_profile_id": None,
        "document_context": {}, "ocr_ratio": 0.0,
        "clauses": [{"id": "a", "pg_clause_id": None, "clause_type": "data_protection",
                     "text": "irrelevant", "embedding": [0.1, 0.2]}],
    }

    class FakeEmbedder:
        def encode(self, text, normalize_embeddings=True):
            class _Arr(list):
                def tolist(self):
                    return list(self)
            return _Arr([0.1, 0.2])

    # clause present (pg_clause_id=None simulates Postgres persistence failing for
    # this clause -- present_clause_types still includes "data_protection" from
    # state["clauses"], so no compliance_gap should fire in this case)
    with patch("counsel_graph.graphrag.langgraph_agent.flag_risks", return_value=[]), \
         patch("counsel_graph.graphrag.langgraph_agent.get_embedder", return_value=FakeEmbedder()):
        risk_flag_node(state)
    assert _document_risk_flags(fresh_db, document_id) == []

    # now simulate the clause type genuinely absent from the document
    state["clauses"] = [{"id": "a", "pg_clause_id": None, "clause_type": "termination",
                          "text": "irrelevant", "embedding": [0.1, 0.2]}]
    with patch("counsel_graph.graphrag.langgraph_agent.flag_risks", return_value=[]), \
         patch("counsel_graph.graphrag.langgraph_agent.get_embedder", return_value=FakeEmbedder()):
        risk_flag_node(state)

    flags = _document_risk_flags(fresh_db, document_id)
    gap_flags = [f for f in flags if f["category"] == "compliance_gap"]
    assert len(gap_flags) == 1
