"""
GraphRAG agent for legal contracts, built on Neo4j + Chroma. Has two compiled
graphs: build_ingestion_graph() extracts clauses/relationships from a PDF and
writes them to Neo4j (pausing for approval), and build_query_graph() answers
a question with semantic search plus a graph query, also pausing for
approval before the answer is final.

Needs NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, ANTHROPIC_API_KEY set.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.types import Command, interrupt

from .neo4j_store import Neo4jGraphStore  # noqa: F401  (re-exported for type hints/back-compat)
from .extraction import (
    extract_clauses,
    detect_conflicts,
    flag_risks,
    detect_missing_clauses,
    generate_cypher,
    synthesize_answer,
)
from ..ingestion.pdf_pipeline import query_collection
from ..resources import get_store, get_embedder, get_chroma_collection, get_checkpointer
from ..retrieval.contract_metadata import extract_contract_metadata, format_executive_summary


# graph 1: ingestion - extract clauses/relationships, persist, human-approve
class IngestionState(TypedDict, total=False):
    # inputs
    document_name: str
    contract_name: Optional[str]
    vendor_name: Optional[str]
    text_chunks: list[dict]  # [{"text": str, "page_start": int, "page_end": int, "section": str|None}, ...]
    org_profile_id: Optional[str]  # resolves required_clause_checklist/risk_threshold_overrides in Postgres
    document_id: Optional[str]     # Postgres document.document_id, for clause/risk_flag rows
    ocr_ratio: Optional[float]     # fraction of pages that needed OCR, used to discount confidentiality confidence

    # intermediate
    job_id: str
    contract_id: str
    document_context: dict        # executive-summary block: parties w/ roles, subject_matter, contract_type
    clauses: list[dict]           # [{"id","clause_type","text","page_start","page_end","section"}, ...]
    same_clause_links: list[dict]
    conflicts: list[dict]
    risk_flags: list[dict]
    missing_clauses: list[dict]
    human_decision: dict

    # output
    status: str


def start_job_node(state: IngestionState) -> dict:
    store = get_store()
    job_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())

    store.create_document_job(job_id, state["document_name"])
    store.create_contract(contract_id, job_id, state["document_name"], state.get("contract_name"))
    if state.get("vendor_name"):
        store.link_vendor(contract_id, state["vendor_name"])

    # pull parties/subject matter/contract type so the approval payload has
    # real context instead of just a filename and a pile of clause UUIDs
    full_text = " ".join(c["text"] for c in state["text_chunks"])
    contract_metadata = extract_contract_metadata(full_text)
    document_context = format_executive_summary(state["document_name"], job_id, contract_metadata)
    document_context["document_id"] = state.get("document_id")

    for party in document_context["parties"]:
        if party.get("name"):
            store.link_party_with_role(contract_id, party["name"], party.get("role"))
    store.set_contract_subject_matter(contract_id, document_context.get("subject_matter"))

    # Persist contract metadata onto Document -- previously this only lived in
    # the transient document_context returned from this job, unavailable to
    # anything reading the document after the job response was gone (e.g. the
    # Decision Brief generator needs key dates/financial terms/parties later).
    if state.get("document_id"):
        try:
            from ..db.repository import update_document
            update_document(
                state["document_id"],
                parties=document_context.get("parties") or [],
                subject_matter=document_context.get("subject_matter"),
                effective_date=document_context.get("effective_date"),
                end_date=document_context.get("end_date"),
                monetary_value=document_context.get("monetary_value"),
                governing_law_country=document_context.get("governing_law_country"),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[start_job] WARNING: persisting contract metadata failed: {type(e).__name__}: {e}")

    store.write_audit_record(job_id, "system", "job_started", f"document={state['document_name']}")

    # Generate the reviewer-facing executive summary once, here, during ingestion
    # rather than on-demand: guideline 5's "executive summary first" requirement
    # means a reviewer should never have to ask a question just to see one, and
    # generating it once at ingestion time (instead of on every later fetch) avoids
    # re-paying an LLM call each time the document is revisited.
    executive_summary = None
    try:
        from ..retrieval.contract_metadata import generate_document_summary
        executive_summary = generate_document_summary(full_text)
        if state.get("document_id"):
            from ..db.repository import add_summary_version
            add_summary_version(state["document_id"], executive_summary, edited_by=None)
    except Exception as e:  # noqa: BLE001
        print(f"[start_job] WARNING: executive summary generation failed: {type(e).__name__}: {e}")
    document_context["executive_summary"] = executive_summary

    # Automatic confidentiality classification (blueprint section 12), run as soon
    # as usable text is available -- same "don't make the reviewer wait" rationale
    # as the executive summary above.
    if state.get("document_id"):
        try:
            from .confidentiality import classify_document_confidentiality
            from ..db.repository import apply_confidentiality_classification
            classification = classify_document_confidentiality(
                full_text,
                document_type=document_context.get("contract_type"),
                document_metadata={"document_name": state["document_name"]},
                ocr_ratio=state.get("ocr_ratio") or 0.0,
            )
            apply_confidentiality_classification(state["document_id"], classification)
            document_context["confidentiality_level"] = classification["level"]
        except Exception as e:  # noqa: BLE001
            print(f"[start_job] WARNING: confidentiality classification failed: {type(e).__name__}: {e}")

    print(f"[start_job] job_id={job_id} contract_id={contract_id}")
    if document_context.get("subject_matter"):
        print(f"[start_job] subject_matter={document_context['subject_matter']}")
    return {"job_id": job_id, "contract_id": contract_id, "document_context": document_context}


def extract_clauses_node(state: IngestionState) -> dict:
    store = get_store()
    embedder = get_embedder()
    clauses: list[dict] = []

    # Postgres persistence is best-effort here: if DATABASE_URL/Postgres isn't
    # reachable, ingestion still completes using Neo4j alone (same non-fatal
    # pattern as this app already uses for Neo4j itself in api/main.py).
    pg_document_id = state.get("document_id")

    for chunk in state["text_chunks"]:
        for raw_clause in extract_clauses(chunk["text"]):
            clause_id = str(uuid.uuid4())
            embedding = embedder.encode(raw_clause["text"], normalize_embeddings=True).tolist()

            store.create_clause(
                clause_id=clause_id,
                contract_id=state["contract_id"],
                text=raw_clause["text"],
                embedding=embedding,
                page_start=chunk.get("page_start", 0),
                page_end=chunk.get("page_end", 0),
                section=chunk.get("section"),
                clause_type=raw_clause.get("clause_type"),
                confidence=raw_clause.get("confidence"),
            )

            for jc in raw_clause.get("judgment_citations", []):
                store.create_judgment(jc["citation"], jc.get("court"), jc.get("year"), None)
                store.link_interpreted_by(clause_id, jc["citation"])

            pg_clause_id = None
            if pg_document_id:
                try:
                    from ..db.repository import upsert_clause
                    parties = raw_clause.get("parties_mentioned") or []
                    pg_clause_id, _created = upsert_clause(
                        document_id=pg_document_id,
                        clause_type=raw_clause.get("clause_type"),
                        extracted_text=raw_clause["text"],
                        page_reference=(
                            str(chunk.get("page_start")) if chunk.get("page_start") == chunk.get("page_end")
                            else f"{chunk.get('page_start')}-{chunk.get('page_end')}"
                        ),
                        party=parties[0] if parties else None,
                        confidence=raw_clause.get("confidence"),
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"[extract_clauses] WARNING: Postgres clause upsert failed: {type(e).__name__}: {e}")

            clauses.append({
                "id": clause_id,
                "pg_clause_id": pg_clause_id,
                "clause_type": raw_clause.get("clause_type"),
                "text": raw_clause["text"],
                "embedding": embedding,
                "page_start": chunk.get("page_start", 0),
                "page_end": chunk.get("page_end", 0),
                "section": chunk.get("section"),
                "confidence": raw_clause.get("confidence"),
            })

    store.write_audit_record(state["job_id"], "system", "clauses_extracted", f"count={len(clauses)}")
    print(f"[extract_clauses] {len(clauses)} clauses extracted")
    return {"clauses": clauses}


def link_same_clause_node(state: IngestionState) -> dict:
    """Vector-searches Neo4j for similar clauses in other contracts and links matches
    above the similarity threshold with SAME_CLAUSE_AS."""
    store = get_store()
    links: list[dict] = []

    for clause in state["clauses"]:
        hits = store.find_similar_clauses(clause["id"], clause["embedding"], top_k=3, min_similarity=0.90)
        for hit in hits:
            store.link_same_clause(clause["id"], hit["clause_id"], hit["score"])
            links.append({"clause_id": clause["id"], "matched_clause_id": hit["clause_id"], "score": hit["score"]})

    store.write_audit_record(state["job_id"], "system", "same_clause_links_created", f"count={len(links)}")
    print(f"[link_same_clause] {len(links)} cross-contract clause links created")
    return {"same_clause_links": links}


def detect_conflicts_node(state: IngestionState) -> dict:
    store = get_store()
    clause_by_id = {c["id"]: c for c in state["clauses"]}
    conflicts = detect_conflicts([{"id": c["id"], "text": c["text"]} for c in state["clauses"]])
    for pair in conflicts:
        store.create_conflict(pair["clause_id_a"], pair["clause_id_b"], pair["reason"])

        # section 13.4: conflicting_terms is a risk category too -- surface it as
        # a real RiskFlag row (attached to the first clause in the pair), not just
        # a Neo4j CONFLICTS_WITH edge a reviewer would never otherwise see.
        pg_document_id = state.get("document_id")
        clause_a = clause_by_id.get(pair["clause_id_a"], {})
        if pg_document_id and clause_a.get("pg_clause_id"):
            try:
                from ..db.repository import create_risk_flag as pg_create_risk_flag
                pg_create_risk_flag(
                    clause_id=clause_a["pg_clause_id"], document_id=None,
                    category="conflicting_terms", severity="high", rationale=pair["reason"],
                    confidence=0.85, recommended_action="Resolve the conflicting clauses before execution.",
                    applicable_rule_source="deterministic:conflict_detection",
                )
            except Exception as e:  # noqa: BLE001
                print(f"[detect_conflicts] WARNING: risk_flag persistence failed: {type(e).__name__}: {e}")

    store.write_audit_record(state["job_id"], "system", "conflicts_detected", f"count={len(conflicts)}")
    print(f"[detect_conflicts] {len(conflicts)} conflicting clause pairs found")
    return {"conflicts": conflicts}


_SENIOR_COUNSEL_SEVERITY_THRESHOLD = "high"  # severity at/above this gets assigned_role=senior_counsel
_SENIOR_COUNSEL_CONFIDENCE_FLOOR = 0.6        # ... OR confidence below this (uncertain + risky needs a second set of eyes)


def _resolve_assigned_role(risk_level: str, confidence: Optional[float],
                            risk_threshold_overrides: dict) -> str:
    """Section 8 routing: risk flags above a severity/confidence threshold are
    assigned to senior_counsel instead of the default reviewer role. Thresholds
    can be overridden per org profile via risk_threshold_overrides."""
    high_bar = risk_threshold_overrides.get("senior_counsel_severity", _SENIOR_COUNSEL_SEVERITY_THRESHOLD)
    confidence_floor = risk_threshold_overrides.get("senior_counsel_confidence_floor", _SENIOR_COUNSEL_CONFIDENCE_FLOOR)
    if risk_level == high_bar:
        return "senior_counsel"
    if confidence is not None and confidence < confidence_floor and risk_level in ("medium", "high"):
        return "senior_counsel"
    return "reviewer"


def risk_flag_node(state: IngestionState) -> dict:
    store = get_store()
    embedder = get_embedder()
    clause_by_id = {c["id"]: c for c in state["clauses"]}
    org_profile_id = state.get("org_profile_id")

    risks = flag_risks([{"id": c["id"], "clause_type": c["clause_type"], "text": c["text"]} for c in state["clauses"]])

    risk_threshold_overrides = {}
    if org_profile_id:
        try:
            from ..db.repository import get_risk_threshold_overrides
            risk_threshold_overrides = get_risk_threshold_overrides(org_profile_id)
        except Exception:
            risk_threshold_overrides = {}

    # Phase 5: resolve the document's standards-hierarchy scope once, reused for
    # every clause below (deviation scoring + playbook fallback both need it).
    # Falls back to org_profile_id-only resolution (Phase 4's behavior) if the
    # document row or its business_unit/geography can't be resolved -- never a
    # hard failure, deviation scoring must keep working either way.
    standards_context = {"org_profile_id": org_profile_id, "business_unit_id": None,
                         "jurisdiction_id": None, "document_type": None, "customer_id": None}
    if state.get("document_id"):
        try:
            from ..db.repository import (
                get_document, resolve_business_unit_id, resolve_jurisdiction_id, get_org_profile_customer_id,
            )
            doc = get_document(state["document_id"])
            if doc:
                standards_context["document_type"] = doc.get("document_type")
                standards_context["business_unit_id"] = resolve_business_unit_id(org_profile_id, doc.get("business_unit"))
                standards_context["jurisdiction_id"] = resolve_jurisdiction_id(doc.get("geography"))
                standards_context["customer_id"] = get_org_profile_customer_id(org_profile_id)
        except Exception as e:  # noqa: BLE001
            print(f"[risk_flag] WARNING: standards context resolution failed: {type(e).__name__}: {e}")

    for r in risks:
        clause = clause_by_id.get(r["clause_id"], {})

        deviation = None
        try:
            from .deviation import compute_deviation
            deviation = compute_deviation(
                clause_text=clause.get("text", ""),
                clause_embedding=clause.get("embedding", []),
                clause_type=clause.get("clause_type"),
                org_profile_id=org_profile_id,
                embedder=embedder,
                standards_context=standards_context,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[risk_flag] WARNING: deviation scoring failed: {type(e).__name__}: {e}")

        confidence_breakdown = None
        try:
            from .confidence import compute_evidence_weighted_confidence
            confidence_breakdown = compute_evidence_weighted_confidence(
                ocr_quality=clause.get("ocr_quality"),
                retrieval_relevance=deviation["deviation_score"] if deviation else None,
                llm_confidence=r.get("confidence"),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[risk_flag] WARNING: confidence composite failed: {type(e).__name__}: {e}")

        assigned_role = _resolve_assigned_role(r["risk_level"], r.get("confidence"), risk_threshold_overrides)

        # section 13.4: link the resolved standard (if any) as this finding's
        # standards evidence, so a reviewer can see what the clause was checked
        # against, not just the LLM's rationale
        standards_evidence = None
        if clause.get("clause_type"):
            try:
                from .standards import resolve_standards
                std_result = resolve_standards(
                    clause_type=clause["clause_type"], document_type=standards_context.get("document_type"),
                    org_profile_id=org_profile_id, business_unit_id=standards_context.get("business_unit_id"),
                    jurisdiction_id=standards_context.get("jurisdiction_id"), customer_id=standards_context.get("customer_id"),
                )
                if std_result.get("selected"):
                    standards_evidence = {"scope_level": std_result["scope_level"],
                                          "standards": [{"knowledge_reference_id": s["knowledge_reference_id"],
                                                         "title": s["title"], "version": s["version"]}
                                                        for s in std_result["selected"]]}
            except Exception as e:  # noqa: BLE001
                print(f"[risk_flag] WARNING: standards evidence lookup failed: {type(e).__name__}: {e}")

        store.create_risk_flag(
            r["clause_id"], r["risk_level"], r["reason"],
            confidence=r.get("confidence"), recommended_action=r.get("recommended_action"),
        )

        pg_clause_id = clause.get("pg_clause_id")
        if pg_clause_id:
            try:
                from ..db.repository import create_risk_flag as pg_create_risk_flag, create_playbook_entry
                pg_flag_id = pg_create_risk_flag(
                    clause_id=pg_clause_id,
                    severity=r["risk_level"],
                    category=r.get("category"),
                    rationale=r["reason"],
                    confidence=r.get("confidence"),
                    recommended_action=r.get("recommended_action"),
                    deviation_score=deviation["deviation_score"] if deviation else None,
                    deviation_detail=deviation,
                    confidence_breakdown=confidence_breakdown,
                    standards_evidence=standards_evidence,
                    applicable_rule_source="llm_risk_review",
                    assigned_role=assigned_role,
                )
                r["deviation"] = deviation
                r["confidence_breakdown"] = confidence_breakdown
                r["assigned_role"] = assigned_role
                r["pg_risk_flag_id"] = pg_flag_id

                if r["risk_level"] in ("medium", "high"):
                    from .playbook import build_playbook_entry
                    entry = build_playbook_entry(
                        clause_text=clause.get("text", ""),
                        clause_type=clause.get("clause_type"),
                        risk_rationale=r["reason"],
                        org_profile_id=org_profile_id,
                        standards_context=standards_context,
                    )
                    create_playbook_entry(
                        risk_flag_id=pg_flag_id,
                        current_language=entry["current_language"],
                        fallback_positions=entry["fallback_positions"],
                        fallback_source=entry["fallback_source"],
                        suggested_redline=entry["suggested_redline"],
                    )
                    r["playbook"] = entry
            except Exception as e:  # noqa: BLE001
                print(f"[risk_flag] WARNING: Postgres risk_flag/playbook persistence failed: {type(e).__name__}: {e}")

    # Section 13.4 deterministic checks: duplicate clauses, auto-renewal, excessive
    # liability, value threshold, unusual governing law. These run independently
    # of the LLM pass above (not a replacement for it) since they catch specific,
    # high-confidence patterns an LLM might phrase inconsistently or miss, and
    # need cross-clause/document-level context flag_risks() doesn't have per-clause.
    pg_document_id = state.get("document_id")
    if pg_document_id:
        try:
            from ..db.repository import create_risk_flag as pg_create_risk_flag
            from .compliance import (
                detect_duplicate_clauses, detect_auto_renewal, detect_excessive_liability,
                check_value_threshold, check_unusual_governing_law,
            )
            document_context = state.get("document_context") or {}
            clauses_with_pg_id = [c for c in state["clauses"] if c.get("pg_clause_id")]

            for dup in detect_duplicate_clauses(clauses_with_pg_id):
                first_clause = clause_by_id.get(dup["clause_ids"][0], {})
                pg_clause_id = first_clause.get("pg_clause_id")
                pg_create_risk_flag(
                    clause_id=pg_clause_id, document_id=None if pg_clause_id else pg_document_id,
                    category="duplicate_clause", severity="medium", rationale=dup["reason"],
                    confidence=1.0, recommended_action="Review all instances and consolidate or confirm intent.",
                    applicable_rule_source="deterministic:duplicate_clause",
                )

            for ren in detect_auto_renewal(clauses_with_pg_id):
                clause = clause_by_id.get(ren["clause_id"], {})
                pg_clause_id = clause.get("pg_clause_id")
                pg_create_risk_flag(
                    clause_id=pg_clause_id, document_id=None if pg_clause_id else pg_document_id,
                    category="auto_renewal", severity="medium",
                    rationale=f"Automatic-renewal language detected: \"{ren['evidence']}\"",
                    confidence=0.9, recommended_action="Confirm renewal terms and notice period are acceptable.",
                    applicable_rule_source="deterministic:auto_renewal",
                )

            for excessive in detect_excessive_liability(clauses_with_pg_id):
                clause = clause_by_id.get(excessive["clause_id"], {})
                pg_clause_id = clause.get("pg_clause_id")
                pg_create_risk_flag(
                    clause_id=pg_clause_id, document_id=None if pg_clause_id else pg_document_id,
                    category="excessive_liability", severity="high",
                    rationale=f"Unlimited/uncapped liability language detected: \"{excessive['evidence']}\"",
                    confidence=0.9, recommended_action="Escalate to senior counsel for liability cap negotiation.",
                    assigned_role="senior_counsel", applicable_rule_source="deterministic:excessive_liability",
                )

            value_flag = check_value_threshold(document_context.get("monetary_value"))
            if value_flag:
                pg_create_risk_flag(
                    clause_id=None, document_id=pg_document_id, category="value_threshold", severity="medium",
                    rationale=value_flag["reason"], confidence=1.0,
                    recommended_action="Route for additional approval per high-value contract policy.",
                    applicable_rule_source="deterministic:value_threshold",
                )

            expected_country = (risk_threshold_overrides.get("expected_governing_law_country")
                                 if risk_threshold_overrides else None)
            if not expected_country and org_profile_id:
                try:
                    from ..db.repository import get_org_profile
                    profile = get_org_profile(org_profile_id)
                    expected_country = (profile or {}).get("jurisdiction_defaults", {}).get("governing_law_country")
                except Exception:
                    expected_country = None
            law_flag = check_unusual_governing_law(document_context.get("governing_law_country"), expected_country)
            if law_flag:
                pg_create_risk_flag(
                    clause_id=None, document_id=pg_document_id, category="unusual_governing_law", severity="medium",
                    rationale=law_flag["reason"], confidence=1.0,
                    recommended_action="Confirm governing-law choice is intentional and acceptable.",
                    applicable_rule_source="deterministic:unusual_governing_law",
                )

            # section 13.4 compliance_gap / prohibited_language: reuse Phase 5's
            # mandatory/prohibited standards, scanned across ALL clause_types at
            # once (not just clause_types present in the document -- a mandatory
            # standard for an ABSENT clause_type is exactly the compliance_gap
            # case, and there's no known clause_type to ask about one at a time
            # for something that was never extracted). Distinct from
            # missing_clause_node's generic checklist since this comes from a
            # jurisdiction/org-mandatory KnowledgeReference rule, not the org
            # profile's required_clause_checklist.
            from .standards import find_all_mandatory_and_prohibited
            present_clause_types = {c.get("clause_type") for c in state["clauses"] if c.get("clause_type")}
            mp_all = find_all_mandatory_and_prohibited(
                document_type=standards_context.get("document_type"), org_profile_id=org_profile_id,
                business_unit_id=standards_context.get("business_unit_id"),
                jurisdiction_id=standards_context.get("jurisdiction_id"), customer_id=standards_context.get("customer_id"),
            )

            mandatory_by_type: dict[str, list[dict]] = {}
            for m in mp_all["mandatory"]:
                mandatory_by_type.setdefault(m["clause_type"], []).append(m)
            for ctype, refs in mandatory_by_type.items():
                if ctype in present_clause_types:
                    continue
                pg_create_risk_flag(
                    clause_id=None, document_id=pg_document_id, category="compliance_gap", severity="high",
                    rationale=f"'{ctype}' is a mandatory requirement per {refs[0]['title'] or 'an applicable standard'}, but is absent from this document.",
                    confidence=1.0, recommended_action="Add the mandatory clause before execution.",
                    standards_evidence={"standards": [{"knowledge_reference_id": s["knowledge_reference_id"], "title": s["title"]} for s in refs]},
                    applicable_rule_source="deterministic:mandatory_standard",
                )

            prohibited_by_type: dict[str, list[dict]] = {}
            for p in mp_all["prohibited"]:
                prohibited_by_type.setdefault(p["clause_type"], []).append(p)
            for ctype, refs in prohibited_by_type.items():
                if ctype not in present_clause_types:
                    continue
                matching_clause = next((cl for cl in state["clauses"] if cl.get("clause_type") == ctype), {})
                matching_pg_clause_id = matching_clause.get("pg_clause_id")
                pg_create_risk_flag(
                    clause_id=matching_pg_clause_id, document_id=None if matching_pg_clause_id else pg_document_id,
                    category="prohibited_language", severity="high",
                    rationale=f"'{ctype}' clause is present but prohibited per {refs[0]['title'] or 'an applicable standard'}.",
                    confidence=1.0, recommended_action="Remove or renegotiate the prohibited clause.",
                    assigned_role="senior_counsel",
                    standards_evidence={"standards": [{"knowledge_reference_id": s["knowledge_reference_id"], "title": s["title"]} for s in refs]},
                    applicable_rule_source="deterministic:prohibited_standard",
                )
        except Exception as e:  # noqa: BLE001
            print(f"[risk_flag] WARNING: deterministic compliance checks failed: {type(e).__name__}: {e}")

    store.write_audit_record(state["job_id"], "system", "risk_flags_created", f"count={len(risks)}")
    print(f"[risk_flag] {len(risks)} risk flags created")
    return {"risk_flags": risks}


def missing_clause_node(state: IngestionState) -> dict:
    """Compares extracted clause types against an expected-clause checklist for
    the document's contract type and flags anything absent."""
    store = get_store()
    contract_type = state.get("document_context", {}).get("contract_type")
    present_types = [c.get("clause_type") for c in state["clauses"]]
    missing = detect_missing_clauses(contract_type, present_types, org_profile_id=state.get("org_profile_id"))

    for m in missing:
        store.create_missing_clause_flag(state["contract_id"], m["clause_type"], m["reason"])

        # section 13.4: missing_clause is a document-level risk category -- surface
        # it as a real RiskFlag row (clause_id=None, document_id set) so a reviewer
        # sees it alongside clause-level findings instead of only in Neo4j.
        pg_document_id = state.get("document_id")
        if pg_document_id:
            try:
                from ..db.repository import create_risk_flag as pg_create_risk_flag
                pg_create_risk_flag(
                    clause_id=None, document_id=pg_document_id, category="missing_clause",
                    severity="medium", rationale=m["reason"], confidence=1.0,
                    recommended_action=f"Add a {m['clause_type'].replace('_', ' ')} clause before execution.",
                    applicable_rule_source="deterministic:missing_clause_checklist",
                )
            except Exception as e:  # noqa: BLE001
                print(f"[missing_clause] WARNING: risk_flag persistence failed: {type(e).__name__}: {e}")

    store.write_audit_record(state["job_id"], "system", "missing_clauses_checked", f"count={len(missing)}")
    print(f"[missing_clause] {len(missing)} expected clause types missing")
    return {"missing_clauses": missing}


_CLAUSE_TEXT_PREVIEW_CHARS = 400  # keeps the approval payload readable; full text is always in Neo4j


def _clause_preview(clause: dict) -> dict:
    """Enough info for a reviewer to judge a clause without looking it up in Neo4j."""
    text = clause.get("text", "")
    truncated = len(text) > _CLAUSE_TEXT_PREVIEW_CHARS
    return {
        "clause_id": clause["id"],
        "clause_type": clause.get("clause_type"),
        "page": (
            clause.get("page_start") if clause.get("page_start") == clause.get("page_end")
            else f"{clause.get('page_start')}-{clause.get('page_end')}"
        ),
        "section": clause.get("section"),
        "text": text[:_CLAUSE_TEXT_PREVIEW_CHARS] + ("..." if truncated else ""),
    }


def human_approval_node(state: IngestionState) -> dict:
    """Pauses via interrupt() with a reviewer summary; resumes on Command(resume=...)
    against the same thread_id. Clause previews are enriched with page/section/text
    rather than bare IDs, since a reviewer can't judge a raw clause_id."""
    clause_by_id = {c["id"]: c for c in state["clauses"]}

    high_risk = [r for r in state["risk_flags"] if r["risk_level"] == "high"]
    high_risk_enriched = [
        {
            **_clause_preview(clause_by_id[r["clause_id"]]),
            "risk_level": r["risk_level"],
            "reason": r["reason"],
            "confidence": r.get("confidence"),
            "recommended_action": r.get("recommended_action"),
            "deviation": r.get("deviation"),
            "confidence_breakdown": r.get("confidence_breakdown"),
            "assigned_role": r.get("assigned_role", "reviewer"),
            "playbook": r.get("playbook"),
        }
        for r in high_risk
        if r["clause_id"] in clause_by_id
    ]

    conflicts_enriched = [
        {
            "reason": c["reason"],
            "clause_a": _clause_preview(clause_by_id[c["clause_id_a"]]) if c["clause_id_a"] in clause_by_id else None,
            "clause_b": _clause_preview(clause_by_id[c["clause_id_b"]]) if c["clause_id_b"] in clause_by_id else None,
        }
        for c in state["conflicts"]
    ]

    payload = {
        "type": "ingestion_approval_request",
        "job_id": state["job_id"],
        "document_context": state.get("document_context", {"document_name": state["document_name"]}),
        "num_clauses": len(state["clauses"]),
        "num_conflicts": len(state["conflicts"]),
        "num_high_risk_flags": len(high_risk),
        "num_missing_clauses": len(state.get("missing_clauses", [])),
        "high_risk_flags": high_risk_enriched,
        "conflicts": conflicts_enriched,
        "missing_clauses": state.get("missing_clauses", []),
        "message": "Review the extracted clauses/conflicts/risk flags/missing clauses below, then "
                    "resume with {'action': 'approve'|'reject'|'escalate', 'reviewer': str, 'comments': str|None}.",
    }
    decision = interrupt(payload)
    return {"human_decision": decision}


def apply_decision_node(state: IngestionState) -> dict:
    store = get_store()
    decision = state["human_decision"]
    reviewer = decision.get("reviewer", "unknown")

    # "approved" (bool) is kept for older callers; "action" is the richer three-way decision
    action = decision.get("action") or ("approve" if decision.get("approved") else "reject")
    approved = action == "approve"

    store.create_reviewer_decision(state["job_id"], approved, reviewer, decision.get("comments"))
    store.set_contract_approval(state["contract_id"], approved if action != "escalate" else None)
    store.update_job_status(state["job_id"], f"{action}d" if action != "approve" else "approved")
    store.write_audit_record(state["job_id"], reviewer, "review_decision", f"action={action}")

    if state.get("document_id"):
        try:
            from ..db.repository import record_review_action, write_audit_log
            record_review_action(
                reviewer_username=reviewer,
                role=decision.get("role", "reviewer"),
                action=action,
                rationale=decision.get("comments"),
                document_id=state["document_id"],
            )
            write_audit_log(
                stage="ingestion_review", actor=reviewer, action=f"decision_{action}",
                details={"job_id": state["job_id"]}, document_id=state["document_id"],
            )
        except Exception as e:  # noqa: BLE001
            print(f"[apply_decision] WARNING: Postgres review_action/audit_log write failed: {type(e).__name__}: {e}")

    print(f"[apply_decision] job {state['job_id']} -> {action} by {reviewer}")
    return {"status": f"{action}d" if action != "approve" else "approved", "document_id": state.get("document_id")}


def build_ingestion_graph():
    graph = StateGraph(IngestionState)
    graph.add_node("start_job", start_job_node)
    graph.add_node("extract_clauses", extract_clauses_node)
    graph.add_node("link_same_clause", link_same_clause_node)
    graph.add_node("detect_conflicts", detect_conflicts_node)
    graph.add_node("risk_flag", risk_flag_node)
    graph.add_node("missing_clause", missing_clause_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("apply_decision", apply_decision_node)

    graph.set_entry_point("start_job")
    graph.add_edge("start_job", "extract_clauses")
    graph.add_edge("extract_clauses", "link_same_clause")
    graph.add_edge("link_same_clause", "detect_conflicts")
    graph.add_edge("detect_conflicts", "risk_flag")
    graph.add_edge("risk_flag", "missing_clause")
    graph.add_edge("missing_clause", "human_approval")
    graph.add_edge("human_approval", "apply_decision")
    graph.add_edge("apply_decision", END)

    # shared checkpointer instance so resume() can find where invoke() paused
    return graph.compile(checkpointer=get_checkpointer())


# graph 2: query - hybrid vector + graph retrieval, then human-approve the answer
class QueryState(TypedDict, total=False):
    # inputs
    question: str
    collection_name: str

    # intermediate
    query_job_id: str
    vector_hits: list[dict]
    cypher_used: str
    cypher_source: str   # "template" or "generated"
    graph_hits: list[dict]
    draft_answer: str
    human_decision: dict

    # output
    final_answer: Optional[str]
    status: str


def start_query_job_node(state: QueryState) -> dict:
    store = get_store()
    job_id = str(uuid.uuid4())
    store.create_query_job(job_id, state["question"])
    store.write_audit_record(job_id, "system", "query_received", state["question"])
    print(f"[start_query_job] job_id={job_id} question={state['question']!r}")
    return {"query_job_id": job_id}


def vector_search_node(state: QueryState) -> dict:
    """Semantic search over the chunked document text (Chroma): 'what does the text say'."""
    collection = get_chroma_collection(state["collection_name"])
    embedder = get_embedder()
    hits = query_collection(collection, embedder, state["question"], top_k=5)
    print(f"[vector_search] {len(hits)} vector hits")
    return {"vector_hits": hits}


# known question shapes get a vetted parameterized query instead of relying
# on the LLM to write correct multi-hop Cypher from scratch every time

_VENDOR_SAME_CLAUSE_JUDGMENTS_TEMPLATE = """
MATCH (c1:Contract)-[:HAS_VENDOR]->(:Party {name: $vendor_name})
MATCH (c1)-[:CONTAINS_CLAUSE]->(cl:Clause)
MATCH (cl)-[:SAME_CLAUSE_AS]-(cl2:Clause)<-[:CONTAINS_CLAUSE]-(c2:Contract)
WHERE c2 <> c1
MATCH (cl)-[:INTERPRETED_BY]->(j:Judgment)
WITH c1, c2, cl, collect(DISTINCT j.citation) AS judgment_citations
WHERE size(judgment_citations) > 1
RETURN c1.name AS vendor_contract, c2.name AS other_contract,
       cl.clause_id AS clause_id, cl.text AS clause_text, judgment_citations
LIMIT 25
"""

_VENDOR_PATTERN = re.compile(
    r"([A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*)*)\s+(?:is|as)\s+the\s+vendor",
)


def match_template(question: str) -> Optional[tuple[str, dict]]:
    """Keyword+regex match for the "vendor X, same clause elsewhere, interpreted by
    multiple judgments" question shape. Returns (cypher, params) or None."""
    q_lower = question.lower()
    if "vendor" in q_lower and "same clause" in q_lower and ("judgment" in q_lower or "judgement" in q_lower):
        vendor_match = _VENDOR_PATTERN.search(question)
        if vendor_match:
            return _VENDOR_SAME_CLAUSE_JUDGMENTS_TEMPLATE, {"vendor_name": vendor_match.group(1).strip()}
    return None


def graph_retrieve_node(state: QueryState) -> dict:
    """Structured multi-hop retrieval over Neo4j: 'how do these things relate'."""
    store = get_store()
    template_match = match_template(state["question"])

    if template_match:
        cypher, params = template_match
        source = "template"
    else:
        cypher = generate_cypher(state["question"])  # raises if it fails the read-only guard
        params = {}
        source = "generated"

    hits = store.run_read_query(cypher, params)
    print(f"[graph_retrieve] source={source} hits={len(hits)}")
    return {"cypher_used": cypher, "cypher_source": source, "graph_hits": hits}


def synthesize_answer_node(state: QueryState) -> dict:
    store = get_store()
    answer = synthesize_answer(state["question"], state["vector_hits"], state["graph_hits"])
    store.write_audit_record(state["query_job_id"], "system", "draft_answer_generated", answer[:500])
    print(f"[synthesize_answer] draft ready ({len(answer)} chars)")
    return {"draft_answer": answer}


def human_approval_node_query(state: QueryState) -> dict:
    """Draft answer waits here for human approval, edit, or rejection."""
    payload = {
        "type": "answer_approval_request",
        "query_job_id": state["query_job_id"],
        "question": state["question"],
        "draft_answer": state["draft_answer"],
        "cypher_used": state["cypher_used"],
        "cypher_source": state["cypher_source"],
        "graph_hits": state["graph_hits"],
        "vector_hits": state["vector_hits"],
        "message": "Resume with {'approved': bool, 'reviewer': str, "
                    "'edited_answer': str|None, 'comments': str|None}.",
    }
    decision = interrupt(payload)
    return {"human_decision": decision}


def finalize_query_node(state: QueryState) -> dict:
    store = get_store()
    decision = state["human_decision"]
    approved = bool(decision.get("approved"))
    reviewer = decision.get("reviewer", "unknown")

    store.create_reviewer_decision(state["query_job_id"], approved, reviewer, decision.get("comments"))
    store.write_audit_record(state["query_job_id"], reviewer, "review_decision", f"approved={approved}")

    if approved:
        final_answer = decision.get("edited_answer") or state["draft_answer"]
        store.store_query_answer(state["query_job_id"], final_answer)
        store.update_job_status(state["query_job_id"], "answered")
    else:
        final_answer = None
        store.update_job_status(state["query_job_id"], "rejected")

    print(f"[finalize_query] job {state['query_job_id']} -> {'answered' if approved else 'rejected'}")
    return {"final_answer": final_answer, "status": "answered" if approved else "rejected"}


def build_query_graph():
    graph = StateGraph(QueryState)
    graph.add_node("start_query_job", start_query_job_node)
    graph.add_node("vector_search", vector_search_node)
    graph.add_node("graph_retrieve", graph_retrieve_node)
    graph.add_node("synthesize_answer", synthesize_answer_node)
    graph.add_node("human_approval", human_approval_node_query)
    graph.add_node("finalize_query", finalize_query_node)

    graph.set_entry_point("start_query_job")

    # vector search and graph retrieval run in parallel, synthesize_answer
    # waits for both since LangGraph blocks a node until all its inbound edges fire
    graph.add_edge("start_query_job", "vector_search")
    graph.add_edge("start_query_job", "graph_retrieve")
    graph.add_edge("vector_search", "synthesize_answer")
    graph.add_edge("graph_retrieve", "synthesize_answer")

    graph.add_edge("synthesize_answer", "human_approval")
    graph.add_edge("human_approval", "finalize_query")
    graph.add_edge("finalize_query", END)

    return graph.compile(checkpointer=get_checkpointer())


# demo usage
if __name__ == "__main__":
    # ingestion demo
    ingestion_app = build_ingestion_graph()
    ingestion_config = {"configurable": {"thread_id": "ingest-demo-1"}}

    result = ingestion_app.invoke(
        {
            "document_name": "abc_ltd_msa.pdf",
            "contract_name": "ABC Ltd Master Service Agreement",
            "vendor_name": "ABC Ltd.",
            "text_chunks": [
                {"text": "This Agreement shall be governed by the laws of India...",
                 "page_start": 3, "page_end": 3, "section": "Governing Law"},
                # ... in practice, pass the text_chunks produced by
                # pdf_rag_pipeline.chunk_pages() / langgraph_pdf_rag_agent.py here.
            ],
        },
        config=ingestion_config,
    )
    print("\n--- PAUSED FOR HUMAN APPROVAL ---")
    print(result["__interrupt__"])

    # Simulates a reviewer approving after inspecting the summary above.
    result = ingestion_app.invoke(
        Command(resume={"approved": True, "reviewer": "jane.doe", "comments": "Looks correct."}),
        config=ingestion_config,
    )
    print("\n--- INGESTION FINAL STATE ---")
    print(result["status"])

    # query demo
    query_app = build_query_graph()
    query_config = {"configurable": {"thread_id": "query-demo-1"}}

    result = query_app.invoke(
        {
            "question": "Show all contracts where ABC Ltd. is the vendor, the same clause "
                        "appears in another contract, and that clause has been interpreted "
                        "by multiple judgments.",
            "collection_name": "abc_ltd_msa_pdf",
        },
        config=query_config,
    )
    print("\n--- PAUSED FOR HUMAN APPROVAL ---")
    print(result["__interrupt__"])

    # Simulates a reviewer approving the LLM's draft answer as-is.
    result = query_app.invoke(
        Command(resume={"approved": True, "reviewer": "jane.doe", "edited_answer": None}),
        config=query_config,
    )
    print("\n--- FINAL ANSWER ---")
    print(result["final_answer"])
