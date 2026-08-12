"""
FastAPI wrapper around the ingestion and query pipelines: HTTP endpoints
plus a small browser UI at /ui instead of the CLI's print()/input() flow.

Run with: uvicorn legal_graphrag.api.main:app --reload --port 8000
Then open http://127.0.0.1:8000/ui (docs at /docs).

Note: with the default in-memory checkpointer this only works with a single
worker process, switch CHECKPOINTER_BACKEND to postgres/redis before running
with --workers > 1.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from ..resources import preload_models, get_store, close_store
from ..guardrails import (
    GuardrailViolation,
    MAX_COMMENT_LENGTH,
    validate_document_upload,
    enforce_page_limit,
)
from ..ingestion.pdf_pipeline import (
    extract_page_content,
    extract_docx_content,
    detect_low_text_pages,
    ocr_pages,
    merge_ocr_results,
    compute_page_sections,
    chunk_pages,
    build_table_records,
    build_table_chunks,
    persist_metadata,
    embed_and_store,
    DEFAULT_METADATA_DIR,
)
from ..retrieval.hybrid_search import invalidate_bm25_cache
from ..graphrag.langgraph_agent import build_ingestion_graph
from ..agents.legal_pipeline import build_legal_agent_graph
from .schemas import JobResponse, ResumeRequest, QueryStartRequest, LoginRequest
from .security import (
    require_session,
    get_current_reviewer,
    rate_limit,
    login_rate_limit,
    verify_credentials,
    create_session_token,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
)

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Cached eval results (tests/eval/run_eval.py's scoring is a live Gemini eval
# against the labeled set, too slow/costly to run on every dashboard load, so
# a manual refresh writes the result here and GET just reads the cache).
EVAL_CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "eval_cache" / "eval_summary.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # build both graphs once at startup so pause/resume actually works
    print("Preloading models (embedder, reranker)...")
    preload_models(include_neo4j=False)  # Neo4j connected separately below

    try:
        from ..db.session import init_db, seed_defaults, seed_demo_standards
        init_db()
        seed_defaults()
        seed_demo_standards()
        print("Postgres tables ready.")
    except Exception as e:
        print(f"WARNING: could not initialize Postgres tables ({type(e).__name__}: {e}). "
              f"Org-profile config, dedup, and review-action persistence will be unavailable "
              f"until DATABASE_URL points at a reachable Postgres instance.")

    try:
        get_store()
        print("Connected to Neo4j.")
    except Exception as e:
        # non-fatal: let the API boot even if Neo4j isn't up yet, just 500 on those routes
        print(
            f"WARNING: could not connect to Neo4j at startup ({type(e).__name__}: {e}). "
            f"The API will still start (/health, /docs, and /ui work now), but any "
            f"endpoint that touches Neo4j will fail until NEO4J_URI/NEO4J_USER/"
            f"NEO4J_PASSWORD in .env point at a reachable instance. No restart needed "
            f"once it's up; the next request will connect then."
        )

    if not os.getenv("SESSION_SECRET") and not os.getenv("API_KEY"):
        print("WARNING: neither SESSION_SECRET nor API_KEY is set, session cookies are "
              "signed with an insecure default. Set SESSION_SECRET in .env before this "
              "is reachable from anywhere but localhost.")

    app.state.ingestion_graph = build_ingestion_graph()
    app.state.query_graph = build_legal_agent_graph()
    print("Ready.")
    yield
    close_store()


# Swagger/ReDoc/OpenAPI are development conveniences, not part of the shipped
# product (see MASTER_BLUEPRINT.md section 6.1) -- disabled whenever
# ENVIRONMENT=production so they aren't reachable in a real deployment.
_IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"

app = FastAPI(
    title="Legal GraphRAG API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

# CORS only matters if you're serving the frontend from a different origin
# than this API (the bundled UI is same-origin so it doesn't need this).
_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _jsonable(obj: Any) -> Any:
    """Belt-and-suspenders JSON sanitization for anything a graph node hands back."""
    return json.loads(json.dumps(obj, default=str))


def _build_job_response(thread_id: str, result: dict, document_context: dict | None = None) -> JobResponse:
    """Checks whether the graph result is paused at an interrupt() or ran to completion.
    document_context (document_name/collection_name/document_id) is known by the caller
    before the graph runs, and isn't part of the interrupt payload itself, so it's merged
    in here for both the paused and completed shapes."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = _jsonable(interrupts[0].value)
        if document_context:
            payload["document_context"] = {**document_context, **payload.get("document_context", {})}
        return JobResponse(
            thread_id=thread_id,
            state="paused",
            checkpoint=payload.get("type"),
            payload=payload,
        )
    result_out = _jsonable(result)
    if document_context:
        result_out["document_context"] = {**document_context, **result_out.get("document_context", {})}
    return JobResponse(thread_id=thread_id, state="completed", result=result_out)


def _run_graph_safely(fn, *args, **kwargs):
    """Turns a graph-invocation exception into a clean 500 instead of an unhandled traceback."""
    try:
        return fn(*args, **kwargs)
    except GuardrailViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001, deliberately broad: this is the API's outermost boundary
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


def _validate_decision_comments(decision: dict) -> None:
    comments = decision.get("comments")
    if comments and len(comments) > MAX_COMMENT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"comments field is {len(comments)} chars, exceeds the {MAX_COMMENT_LENGTH}-char limit.",
        )


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/ui")


@app.get("/ui", include_in_schema=False)
async def ui() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# auth: fixed reviewer roster (admin/reviewer/senior_counsel), no user database, session cookie only
@app.post("/api/auth/login", dependencies=[Depends(login_rate_limit)])
async def login(body: LoginRequest, response: Response) -> dict:
    reviewer = verify_credentials(body.username, body.password)
    if reviewer is None:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = create_session_token(reviewer.username, reviewer.role)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"status": "ok", "username": reviewer.username, "role": reviewer.role}


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/api/auth/me", dependencies=[Depends(require_session)])
async def me(reviewer: dict = Depends(get_current_reviewer)) -> dict:
    """Lets the frontend check whether the current session is still valid, and
    which username/role it's logged in as (for role-aware review-queue filtering)."""
    return {"status": "ok", **reviewer}


# ingestion pipeline routes
@app.post("/api/ingestion/jobs", response_model=JobResponse, dependencies=[Depends(require_session), Depends(rate_limit)])
async def start_ingestion_job(
    file: UploadFile = File(...),
    vendor_name: str | None = Form(None),
    contract_name: str | None = Form(None),
    collection_name: str | None = Form(None),
    org_profile_id: str | None = Form(None),
    business_unit: str | None = Form(None),
    counterparty: str | None = Form(None),
    geography: str | None = Form(None),
    confidentiality_level: str | None = Form(None),
    review_priority: str | None = Form(None),
) -> JobResponse:
    """Uploads a PDF or DOCX, runs it through extraction/OCR/chunking/embedding, then starts
    the ingestion graph. Blocks until the graph pauses for approval, fine for a demo,
    but a real deployment should push this to a background task/queue instead."""
    raw_bytes = await file.read()
    try:
        ext = validate_document_upload(file.filename, file.content_type, len(raw_bytes))
    except GuardrailViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    document_name = file.filename or f"upload-{uuid.uuid4().hex}{ext}"

    from ..storage import object_store
    storage_key = None
    if object_store.is_configured():
        try:
            object_store.ensure_bucket()
            storage_key = object_store.put_object(raw_bytes, document_name)
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: object store upload failed ({type(e).__name__}: {e}), falling back to local disk.")

    saved_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{document_name}"
    if storage_key is None:
        saved_path.write_bytes(raw_bytes)
    else:
        # still keep a local temp copy for this request's own pipeline run, so
        # OCR/pdfplumber extraction below (which need a real file path) work the
        # same whether or not the object store is configured. It's removed after.
        saved_path.write_bytes(raw_bytes)

    pg_document_id = None
    try:
        from ..db.repository import create_document
        pg_document_id = create_document(
            filename=document_name,
            storage_key=storage_key,
            collection_name=collection_name or re.sub(r"\W+", "_", document_name),
            document_type=None,
            business_unit=business_unit,
            counterparty=counterparty,
            geography=geography,
            confidentiality_level=confidentiality_level,
            review_priority=review_priority,
            org_profile_id=org_profile_id,
            status="processing",
        )
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: Postgres document row creation failed: {type(e).__name__}: {e}")

    resolved_collection_name = collection_name or re.sub(r"\W+", "_", document_name)

    def _ingest():
        if ext == ".docx":
            pages, raw_tables = extract_docx_content(str(saved_path))
        else:
            pages, raw_tables = extract_page_content(str(saved_path))
        enforce_page_limit(len(pages))

        ocr_ratio = 0.0
        if ext == ".pdf":
            low_text_pages = detect_low_text_pages(pages)
            if low_text_pages:
                ocr_results = ocr_pages(str(saved_path), low_text_pages)
                pages = merge_ocr_results(pages, ocr_results)
            ocr_ratio = len(low_text_pages) / len(pages) if pages else 0.0

        page_sections = compute_page_sections(pages)
        text_chunks = chunk_pages(pages, document_name, page_sections)
        table_records = build_table_records(raw_tables, document_name, page_sections)
        table_chunks = build_table_chunks(table_records, start_chunk_index=len(text_chunks))
        all_chunks = text_chunks + table_chunks

        persist_metadata(all_chunks, os.path.join(DEFAULT_METADATA_DIR, f"{document_name}.metadata.json"))
        embed_and_store(all_chunks, resolved_collection_name)
        invalidate_bm25_cache(resolved_collection_name)

        graphrag_chunks = [
            {"text": c.text, "page_start": c.page_start, "page_end": c.page_end, "section": c.section}
            for c in text_chunks
        ]

        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        result = app.state.ingestion_graph.invoke(
            {
                "document_name": document_name,
                "contract_name": contract_name,
                "vendor_name": vendor_name,
                "text_chunks": graphrag_chunks,
                "org_profile_id": org_profile_id,
                "document_id": pg_document_id,
                "ocr_ratio": ocr_ratio,
            },
            config=config,
        )
        return thread_id, result

    thread_id, result = _run_graph_safely(_ingest)

    if pg_document_id:
        try:
            from ..db.repository import update_document
            update_document(
                pg_document_id,
                job_id=result.get("job_id"),
                contract_id=result.get("contract_id"),
                document_type=(result.get("document_context") or {}).get("contract_type"),
                status="awaiting_review" if result.get("__interrupt__") else result.get("status", "processing"),
            )
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: Postgres document row update failed: {type(e).__name__}: {e}")

    return _build_job_response(
        thread_id,
        result,
        document_context={
            "document_name": document_name,
            "collection_name": resolved_collection_name,
            "document_id": pg_document_id,
        },
    )


@app.post("/api/ingestion/jobs/{thread_id}/resume", response_model=JobResponse, dependencies=[Depends(require_session), Depends(rate_limit)])
async def resume_ingestion_job(thread_id: str, body: ResumeRequest) -> JobResponse:
    _validate_decision_comments(body.decision)
    config = {"configurable": {"thread_id": thread_id}}
    snapshot_before = app.state.ingestion_graph.get_state(config)
    result = _run_graph_safely(
        app.state.ingestion_graph.invoke, Command(resume=body.decision), config=config
    )
    prior_values = snapshot_before.values if snapshot_before else {}
    return _build_job_response(
        thread_id,
        result,
        document_context={
            "document_name": prior_values.get("document_name"),
            "document_id": prior_values.get("document_id"),
        },
    )


@app.get("/api/ingestion/jobs/{thread_id}", dependencies=[Depends(require_session)])
async def get_ingestion_job_status(thread_id: str) -> dict:
    """Reads the current LangGraph checkpoint state for this thread (read-only, no side effects)."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = app.state.ingestion_graph.get_state(config)
    if snapshot is None or snapshot.values == {}:
        raise HTTPException(status_code=404, detail=f"No ingestion job found for thread_id={thread_id}")
    return _jsonable({
        "thread_id": thread_id,
        "job_id": snapshot.values.get("job_id"),
        "status": snapshot.values.get("status"),
        "next_node": snapshot.next,
    })


# query pipeline routes (router -> retrieval -> auditor -> synthesizer)
@app.post("/api/query/jobs", response_model=JobResponse, dependencies=[Depends(require_session), Depends(rate_limit)])
async def start_query_job(body: QueryStartRequest) -> JobResponse:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    result = _run_graph_safely(
        app.state.query_graph.invoke,
        {
            "question": body.question,
            "collection_name": body.collection_name,
            "metadata_filter": body.metadata_filter,
        },
        config=config,
    )
    return _build_job_response(thread_id, result)


@app.post("/api/query/jobs/{thread_id}/resume", response_model=JobResponse, dependencies=[Depends(require_session), Depends(rate_limit)])
async def resume_query_job(thread_id: str, body: ResumeRequest) -> JobResponse:
    """Resumes either checkpoint in the query graph (evidence or answer). Client just
    keeps calling this with the reviewer's decision until state == "completed"."""
    _validate_decision_comments(body.decision)
    config = {"configurable": {"thread_id": thread_id}}
    result = _run_graph_safely(
        app.state.query_graph.invoke, Command(resume=body.decision), config=config
    )
    return _build_job_response(thread_id, result)


@app.get("/api/query/jobs/{thread_id}", dependencies=[Depends(require_session)])
async def get_query_job_status(thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = app.state.query_graph.get_state(config)
    if snapshot is None or snapshot.values == {}:
        raise HTTPException(status_code=404, detail=f"No query job found for thread_id={thread_id}")
    return _jsonable({
        "thread_id": thread_id,
        "query_job_id": snapshot.values.get("query_job_id"),
        "route": snapshot.values.get("route"),
        "status": snapshot.values.get("status"),
        "answer_revision_count": snapshot.values.get("answer_revision_count"),
        "next_node": snapshot.next,
    })


# audit trail: read-only, works for either a DocumentJob (job_id) or QueryJob (query_job_id)
@app.get("/api/audit/{job_id}", dependencies=[Depends(require_session)])
async def get_audit_trail(job_id: str) -> dict:
    store = get_store()
    audit_records = store.get_audit_trail(job_id)
    if not audit_records:
        raise HTTPException(status_code=404, detail=f"No audit records found for job_id={job_id}")
    reviewer_decisions = store.get_reviewer_decisions(job_id)
    return _jsonable({
        "job_id": job_id,
        "audit_trail": audit_records,
        "reviewer_decisions": reviewer_decisions,
    })


# legal operations dashboard: aggregate throughput/approval/risk metrics
@app.get("/api/dashboard/stats", dependencies=[Depends(require_session)])
async def get_dashboard_stats() -> dict:
    store = get_store()
    return _jsonable(store.get_dashboard_stats())


# org profiles: dropdown source for the Upload Workspace screen
@app.get("/api/org-profiles", dependencies=[Depends(require_session)])
async def list_org_profiles_endpoint() -> dict:
    from ..db.repository import list_org_profiles
    return _jsonable({"org_profiles": list_org_profiles()})


# confidentiality -> access control (section 12.4): which roles may view a
# document at each level. Adapts the existing admin/senior_counsel/reviewer
# roles rather than inventing a new permission system -- highly_confidential
# is senior_counsel/admin only, everything else is any authenticated reviewer.
_CONFIDENTIALITY_ACCESS_ROLES = {
    "highly_confidential": ("senior_counsel", "admin"),
    "confidential": ("reviewer", "senior_counsel", "admin"),
    "internal": ("reviewer", "senior_counsel", "admin"),
    "public": ("reviewer", "senior_counsel", "admin"),
}


def _enforce_confidentiality_access(confidentiality_level: str | None, reviewer_role: str) -> None:
    allowed_roles = _CONFIDENTIALITY_ACCESS_ROLES.get(confidentiality_level or "internal", _CONFIDENTIALITY_ACCESS_ROLES["internal"])
    if reviewer_role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"This document is classified '{confidentiality_level}'; role '{reviewer_role}' is not authorized to view it.",
        )


# document detail: clauses/risk-flags/playbook entries for the Review Workspace screen
@app.get("/api/documents/{document_id}", dependencies=[Depends(require_session)])
async def get_document_detail(document_id: str, reviewer: dict = Depends(get_current_reviewer)) -> dict:
    from ..db.session import get_session
    from ..db.models import Document, Clause, RiskFlag, PlaybookEntry
    from sqlalchemy import select

    with get_session() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"No document found for document_id={document_id}")
        _enforce_confidentiality_access(doc.confidentiality_level, reviewer["role"])

        from ..db.repository import get_summary_history, get_confidentiality_history
        summary_versions = get_summary_history(document_id)
        executive_summary = summary_versions[0]["summary_text"] if summary_versions else None

        clauses = session.execute(select(Clause).where(Clause.document_id == document_id)).scalars().all()
        clause_payload = []
        for c in clauses:
            flags = session.execute(select(RiskFlag).where(RiskFlag.clause_id == c.clause_id)).scalars().all()
            flag_payload = []
            for f in flags:
                playbook = session.execute(
                    select(PlaybookEntry).where(PlaybookEntry.risk_flag_id == f.risk_flag_id)
                ).scalar_one_or_none()
                flag_payload.append({
                    "risk_flag_id": f.risk_flag_id, "severity": f.severity, "rationale": f.rationale,
                    "confidence": f.confidence, "recommended_action": f.recommended_action,
                    "deviation_score": f.deviation_score, "deviation_detail": f.deviation_detail,
                    "confidence_breakdown": f.confidence_breakdown, "assigned_role": f.assigned_role,
                    "reviewer_status": f.reviewer_status,
                    "playbook": ({
                        "current_language": playbook.current_language,
                        "fallback_positions": playbook.fallback_positions,
                        "fallback_source": playbook.fallback_source,
                        "suggested_redline": playbook.suggested_redline,
                    } if playbook else None),
                })
            clause_payload.append({
                "clause_id": c.clause_id, "clause_type": c.clause_type, "extracted_text": c.extracted_text,
                "page_reference": c.page_reference, "party": c.party, "confidence": c.confidence,
                "version": c.version, "risk_flags": flag_payload,
            })

        override_history = get_confidentiality_history(document_id)

        return _jsonable({
            "document_id": doc.document_id, "filename": doc.filename, "document_type": doc.document_type,
            "collection_name": doc.collection_name,
            "business_unit": doc.business_unit, "counterparty": doc.counterparty, "geography": doc.geography,
            "confidentiality_level": doc.confidentiality_level,
            "confidentiality_confidence": doc.confidentiality_confidence,
            "confidentiality_source": doc.confidentiality_source,
            "confidentiality_reasons": doc.confidentiality_reasons,
            "confidentiality_needs_confirmation": doc.confidentiality_needs_confirmation,
            "confidentiality_history": override_history,
            "review_priority": doc.review_priority,
            "org_profile_id": doc.org_profile_id, "status": doc.status, "job_id": doc.job_id,
            "contract_id": doc.contract_id, "clauses": clause_payload,
            "executive_summary": executive_summary,
        })


class ReviewActionRequest(BaseModel):
    action: str = Field(..., description="accept/edit/reject/escalate/comment")
    rationale: str | None = None
    document_id: str | None = None
    clause_id: str | None = None
    risk_flag_id: str | None = None


@app.post("/api/review-actions", dependencies=[Depends(require_session)])
async def create_review_action(body: ReviewActionRequest, reviewer: dict = Depends(get_current_reviewer)) -> dict:
    from ..db.repository import get_risk_flag, record_review_action
    from .security import can_act_on_assigned_role

    if body.risk_flag_id:
        flag = get_risk_flag(body.risk_flag_id)
        if flag is None:
            raise HTTPException(status_code=404, detail=f"No risk flag found for risk_flag_id={body.risk_flag_id}")
        if not can_act_on_assigned_role(reviewer["role"], flag["assigned_role"]):
            raise HTTPException(
                status_code=403,
                detail=f"This risk flag is routed to {flag['assigned_role']}; your role ({reviewer['role']}) "
                       f"isn't senior enough to act on it.",
            )

    review_action_id = record_review_action(
        reviewer_username=reviewer["username"], role=reviewer["role"], action=body.action,
        rationale=body.rationale, document_id=body.document_id, clause_id=body.clause_id,
        risk_flag_id=body.risk_flag_id,
    )
    return {"status": "ok", "review_action_id": review_action_id}


class SummaryVersionRequest(BaseModel):
    summary_text: str = Field(..., min_length=1, max_length=50000)


@app.post("/api/documents/{document_id}/summary", dependencies=[Depends(require_session)])
async def add_summary_version_endpoint(document_id: str, body: SummaryVersionRequest,
                                        reviewer: dict = Depends(get_current_reviewer)) -> dict:
    from ..db.repository import add_summary_version
    return add_summary_version(document_id, body.summary_text, edited_by=reviewer["username"])


@app.get("/api/documents/{document_id}/summary", dependencies=[Depends(require_session)])
async def get_summary_history_endpoint(document_id: str) -> dict:
    from ..db.repository import get_summary_history
    return {"versions": get_summary_history(document_id)}


# confidentiality override (section 12.3): only senior_counsel/admin may override,
# reason is required, every override is audited via ConfidentialityOverride
CONFIDENTIALITY_LEVELS = ("public", "internal", "confidential", "highly_confidential")
_OVERRIDE_ROLES = ("senior_counsel", "admin")


class ConfidentialityOverrideRequest(BaseModel):
    new_level: str = Field(..., description="public/internal/confidential/highly_confidential")
    reason: str = Field(..., min_length=1, max_length=2000)


@app.post("/api/documents/{document_id}/confidentiality", dependencies=[Depends(require_session)])
async def override_confidentiality_endpoint(document_id: str, body: ConfidentialityOverrideRequest,
                                             reviewer: dict = Depends(get_current_reviewer)) -> dict:
    if reviewer["role"] not in _OVERRIDE_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{reviewer['role']}' is not authorized to override confidentiality classification.",
        )
    if body.new_level not in CONFIDENTIALITY_LEVELS:
        raise HTTPException(status_code=400, detail=f"new_level must be one of {CONFIDENTIALITY_LEVELS}")

    from ..db.repository import override_confidentiality
    try:
        return override_confidentiality(document_id, body.new_level, body.reason, reviewer["username"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/documents/{document_id}/confidentiality", dependencies=[Depends(require_session)])
async def get_confidentiality_history_endpoint(document_id: str) -> dict:
    from ..db.repository import get_confidentiality_history
    return {"history": get_confidentiality_history(document_id)}


# standards resolution (section 11): resolves the applicable approved-clause
# standard for a document's context through the 8-level hierarchy, given a
# document_id (its business_unit/geography/document_type/org_profile_id are
# read from the row) and a clause_type to resolve.
@app.get("/api/documents/{document_id}/standards", dependencies=[Depends(require_session)])
async def resolve_document_standards_endpoint(document_id: str, clause_type: str) -> dict:
    from ..db.repository import (
        get_document, resolve_business_unit_id, resolve_jurisdiction_id, get_org_profile_customer_id,
    )
    from ..graphrag.standards import resolve_standards, check_mandatory_and_prohibited

    doc = get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"No document found for document_id={document_id}")

    org_profile_id = doc.get("org_profile_id")
    business_unit_id = resolve_business_unit_id(org_profile_id, doc.get("business_unit"))
    jurisdiction_id = resolve_jurisdiction_id(doc.get("geography"))
    customer_id = get_org_profile_customer_id(org_profile_id)

    result = resolve_standards(
        clause_type=clause_type, document_type=doc.get("document_type"), org_profile_id=org_profile_id,
        business_unit_id=business_unit_id, jurisdiction_id=jurisdiction_id, customer_id=customer_id,
    )
    result["mandatory_and_prohibited"] = check_mandatory_and_prohibited(
        clause_type=clause_type, document_type=doc.get("document_type"), org_profile_id=org_profile_id,
        business_unit_id=business_unit_id, jurisdiction_id=jurisdiction_id, customer_id=customer_id,
    )
    return _jsonable(result)


# cross-portfolio conflict detection (section 6): scope by org_profile_id or
# counterparty (Postgres), pull those contracts' clauses (Neo4j), compare terms
@app.get("/api/portfolio/conflicts", dependencies=[Depends(require_session)])
async def get_portfolio_conflicts(org_profile_id: str | None = None, counterparty: str | None = None) -> dict:
    if not org_profile_id and not counterparty:
        raise HTTPException(status_code=400, detail="Provide org_profile_id and/or counterparty to scope the comparison.")

    from ..graphrag.portfolio_conflicts import find_conflicting_clause_pairs
    from ..db.session import get_session
    from ..db.models import Document
    from sqlalchemy import select

    with get_session() as session:
        stmt = select(Document).where(Document.contract_id.isnot(None))
        if org_profile_id:
            stmt = stmt.where(Document.org_profile_id == org_profile_id)
        if counterparty:
            stmt = stmt.where(Document.counterparty == counterparty)
        docs = session.execute(stmt).scalars().all()
        contract_ids = [d.contract_id for d in docs]

    if len(contract_ids) < 2:
        return _jsonable({"conflicts": [], "note": "Fewer than 2 documents in scope; nothing to compare."})

    store = get_store()
    clause_rows = store.find_same_type_clauses_across_contracts(contract_ids)
    conflicts = find_conflicting_clause_pairs(clause_rows)
    return _jsonable({"conflicts": conflicts, "documents_compared": len(contract_ids)})


# evaluation matrix: clause recall / risk-flag precision / missing-clause accuracy
# against the labeled eval set (tests/eval/run_eval.py), for the Operations Dashboard.
def _summarize_eval_results(results: list[dict]) -> dict:
    scored_recalls = [r["clause_recall"] for r in results if r["clause_recall"] is not None]
    avg_recall = sum(scored_recalls) / len(scored_recalls) if scored_recalls else None
    avg_precision = sum(r["risk_precision"] for r in results) / len(results) if results else None
    missing_correct = sum(1 for r in results if r["missing_clause_detection_correct"])
    avg_missing_accuracy = missing_correct / len(results) if results else None
    return {
        "documents": [
            {
                "filename": r["filename"],
                "clause_recall": r["clause_recall"],
                "risk_precision": r["risk_precision"],
                "missing_clause_detection_correct": r["missing_clause_detection_correct"],
            }
            for r in results
        ],
        "average_clause_recall": avg_recall,
        "average_risk_precision": avg_precision,
        "average_missing_clause_accuracy": avg_missing_accuracy,
    }


@app.get("/api/eval/summary", dependencies=[Depends(require_session)])
async def get_eval_summary() -> dict:
    """Reads the cached eval run written by /api/eval/refresh. Returns a note
    (not a 404) when no cache exists yet, so the dashboard can render an
    explanatory empty state instead of an error."""
    if not EVAL_CACHE_PATH.exists():
        return {"computed": False, "note": "No eval run cached yet. Trigger a refresh to compute one."}
    cached = json.loads(EVAL_CACHE_PATH.read_text(encoding="utf-8"))
    return {"computed": True, **cached}


@app.post("/api/eval/refresh", dependencies=[Depends(require_session)])
async def refresh_eval_summary() -> dict:
    """Runs the real eval (live Gemini calls against tests/eval/labeled_eval_set.json,
    can take a couple minutes) and caches the summary to disk. Manual trigger only,
    not run automatically, since it's slow and costs LLM calls."""
    import sys
    eval_dir = str(Path(__file__).resolve().parents[3] / "tests" / "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    try:
        from run_eval import main as run_eval_main
        results = run_eval_main()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Eval run failed: {type(e).__name__}: {e}") from e

    summary = _summarize_eval_results(results)
    EVAL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_CACHE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"computed": True, **summary}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
