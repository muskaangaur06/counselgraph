# CounselGraph Implementation Status

## Repository
- Path: D:\LegalAssistant
- Branch: main
- Latest commit: eefe70a (Add project control files), 2 commits ahead of origin/main, not pushed
- Working tree: clean
- Last updated: 2026-08-12

## Environment
- Python: 3.10 (global at C:\Program Files\Python310), .venv created at D:\LegalAssistant\.venv using this interpreter
- Virtual environment: .venv created and verified this session. python/pip both resolve inside .venv. 175 packages installed, `pip check` clean.
- Docker Compose: 4 services (postgres, neo4j, minio, api), all containers Up and healthy, running 6+ hours prior to this session
- Database: PostgreSQL 16-alpine, container healthy, DATABASE_URL configurable, falls back to local SQLite if unset
- Neo4j: neo4j:5-community, container healthy. NOTE: hostname `neo4j` only resolves inside the Docker network -- running tests/scripts from the host (.venv) needs NEO4J_URI pointed at `localhost:7687` instead, or requests time out via DNS retries.
- Chroma: embedded/PersistentClient mode inside the api container (not a separate service), volume-mounted at /app/data/chroma_db. No duplicate Chroma deployment needed.
- MinIO: minio/minio:latest, container healthy, S3 API + console
- Gemini: google-genai==1.73.1 in requirements; GEMINI_API_KEY required for live LLM calls, not verified/tested this session (untouched per credential policy)
- Frontend delivery model: single FastAPI app serves static index.html directly (`/`, `/ui` routes) plus all `/api/*` routes -- single-site delivery already in place, no separate frontend app or reverse proxy

## Current Verified Capabilities
| Capability | Status | Evidence/Test | Notes |
|---|---|---|---|
| Upload -> ingestion pipeline | Working | test_integration_ingestion.py (partial pass, see failures) | LangGraph-based, human-in-the-loop pause/resume |
| PDF/DOCX extraction, OCR | Present | pdf2image/pdfplumber/pytesseract in requirements, Dockerfile installs poppler-utils+tesseract-ocr | Not independently smoke-tested this session |
| Clause extraction | Present | src/legal_graphrag/graphrag/extraction.py | |
| Chroma + Neo4j RAG retrieval | Present | src/legal_graphrag/graphrag/neo4j_store.py, embedded Chroma | Neo4j reachable from container network only, see env constraint above |
| Risk flagging | Present | RiskFlag model, confidence.py | |
| Deviation scoring (cosine similarity) | Present | src/legal_graphrag/graphrag/deviation.py | |
| Negotiation playbook generation | Present | src/legal_graphrag/graphrag/playbook.py, PlaybookEntry model | |
| Cross-portfolio conflict detection | Present | src/legal_graphrag/graphrag/portfolio_conflicts.py, GET /api/portfolio/conflicts | |
| Evidence-weighted confidence | Present | RiskFlag.confidence_breakdown JSON field | |
| Executive summary generation | Present, recently enhanced | uncommitted langgraph_agent.py change: now generated once at ingestion time, stored via SummaryVersion | |
| Summary versioning backend | Present | SummaryVersion model, add_summary_version, GET/POST /api/documents/{id}/summary | No frontend editor yet (blueprint section 14.1 gap) |
| Fixed reviewer roster auth | Present | security.py: REVIEWERS json or REVIEWER_N_* env vars, roles admin/reviewer/senior_counsel | No self-signup, matches blueprint 3.7 |
| Audit log | Present | AuditLog model, append-only, GET /api/audit/{job_id} | |
| Ask/Q&A (single-shot, not multi-turn) | Present but NOT multi-turn chat | index.html handleAskResponse / askThreadId flow | Requires manually-entered `askCollection` (auto-filled by shared doc context, but not read-only). No conversational memory across questions -- blueprint's "in-progress chat conversion" has NOT landed; this is still single-question-per-thread. |
| Shared document context (frontend) | Present | index.html `currentDocument` global object + setCurrentDocument(), auto-fills askCollection/auditJobId/documentDetailId | Auto-fill confirmed real. Fields are pre-filled but still editable text inputs, not read-only metadata (blueprint 17.2 gap). |
| Eval matrix / dashboard panel | Present, cache-based | GET /api/eval/summary (reads cache), POST /api/eval/refresh (live Gemini eval, writes cache) | Only 3 metrics (clause_recall, risk_precision, missing_clause_detection_correct), not the full metric suite in blueprint sections 21-28 |
| Confidentiality classification | Manual only | Document.confidentiality_level field exists, set via org_profile.confidentiality_default | No automatic hybrid classifier (deterministic + Gemini), no override audit trail -- blueprint section 12 is genuinely missing |
| Multi-tenancy (customer/org/BU) | Partial (Phase 1 done) | tests/test_tenancy_migration.py, verified against live Postgres | Customer/Jurisdiction/BusinessUnit tables added, org_profile.customer_id backfilled to Tata Group. No DocumentType table yet. Query-layer tenant scoping/isolation not wired in (single customer today, nothing to isolate against yet) -- deferred to Phase 4+. |
| Decision Brief + approval chain | Missing | No DecisionBrief or ApprovalChain models/routes | Blueprint section 15 genuinely missing |
| Standards resolution hierarchy | Partial | KnowledgeReference has business_unit_scope/jurisdiction_scope/approval_status fields, but no deterministic resolution service implementing the 8-level precedence in blueprint 11.1 | |
| Swagger/OpenAPI in production | Not gated | FastAPI() call has no docs_url/redoc_url/openapi_url environment guard | Blueprint section 6.1 gap, real and unconditional |
| Public registration | N/A (never existed) | No register routes found | Already compliant with blueprint 16.1 |
| Non-root Docker user | Missing | Dockerfile runtime stage runs as root | Blueprint 7.3 gap |
| API healthcheck | Missing | docker-compose.yml has healthchecks for postgres/neo4j/minio but not for `api` service | Blueprint 32.2 gap |

## Current Phase
- Phase: 1 (Architecture and migration audit) -- COMPLETE
- Objective: Add Customer/Organization/BusinessUnit/Jurisdiction tenancy model without breaking any of the 15+ existing call sites keyed on org_profile_id
- Files changed: src/legal_graphrag/db/models.py (Customer, Jurisdiction, BusinessUnit models; customer_id/industry_sector/default_risk_tolerance/is_active added to OrgProfile), src/legal_graphrag/db/session.py (idempotent _add_missing_columns() column retrofit, seed_defaults()), src/legal_graphrag/api/main.py (calls seed_defaults() in lifespan), tests/test_tenancy_migration.py (new, 5 tests)
- Focused tests: tests/test_tenancy_migration.py (5 passed); full fast suite (24 passed, tests/eval and test_integration_ingestion.py excluded -- both require live GEMINI_API_KEY/reachable Neo4j from host, pre-existing environment gap not caused by this phase)
- Blockers: none

## Phase 1 Decisions
- org_profile IS the "Organization" from the blueprint (e.g. "Vendor Procurement Unit" is an Organization under Tata Group), not a new Business Unit layer. Chosen because org_profile_id is referenced by 15+ call sites (extraction.py, deviation.py, playbook.py, repository.py, langgraph_agent.py) -- this mapping required zero changes to any of them.
- No Alembic. Added a small idempotent column-retrofit helper (_add_missing_columns in session.py) that diffs each model's columns against live table columns via SQLAlchemy inspector and ALTER TABLE ADDs what's missing, with the column's Python-side default carried into the DDL so retrofitted existing rows don't end up NULL (e.g. is_active retrofits to true, not NULL).
- seed_defaults() is get-or-create by natural unique key (customer.code, jurisdiction.code) and only backfills org_profile rows where customer_id IS NULL, so a profile already assigned to some other customer is never silently reassigned (tested).
- Did not add customer_id to Document directly. Every document already carries org_profile_id (currently NULL for all 4 existing rows), and customer scope is derivable transitively via org_profile.customer_id. Revisit only if Phase 4/9 tenant-filtered query performance actually needs a direct denormalized index.
- Did not add a DocumentType table this phase -- not blocking anything in Phase 1's scope, deferred to whichever phase first needs it (likely Phase 4/5).
- Query-layer tenant isolation (section 10's negative tests: cross-org access denial, vector-retrieval tenant filtering) is NOT implemented yet -- there is exactly one customer and no auth-role-to-org mapping today, so there is nothing to isolate against. Revisit once Phase 2 (auth/access control) and Phase 5 (tenant-filtered RAG) land.

## Phase History
| Phase | Status | Commit | Tests | Notes |
|---|---|---|---|---|
| 0 | Complete | ab1ae7e, eefe70a | 22 passed, 2 failed / 24 total, 728s | Failures: neo4j DNS resolution from host process, not an app bug. See Environment Constraint below. |
| 1 | Complete | pending (this phase not yet committed) | 24 passed (19 pre-existing + 5 new tenancy tests), verified against live Postgres too | Customer/Jurisdiction/BusinessUnit tables added, org_profile backfilled. See Phase 1 Decisions above. |

## Committed Changes (pre-existing at session start, reviewed then committed)
Reviewed via `git diff`, not discarded, committed as ab1ae7e "Fix document context threading":
- `src/legal_graphrag/api/main.py`: adds `document_context` merging into job responses (threads document_name/collection_name/document_id through pause+resume), adds executive_summary to document detail response, adds `/api/eval/summary` and `/api/eval/refresh` endpoints reading/writing a JSON eval cache.
- `src/legal_graphrag/graphrag/langgraph_agent.py`: generates the executive summary once at ingestion time (start_job_node) instead of on-demand, persists via SummaryVersion; threads document_id through apply_decision_node's return.
- `src/legal_graphrag/api/static/index.html`: 413 insertions / 33 deletions, not line-by-line reviewed in detail beyond the `currentDocument` shared-state mechanism confirmed above.

MASTER_BLUEPRINT.md and IMPLEMENTATION_STATUS.md committed separately as eefe70a "Add project control files". Neither commit pushed to origin yet.

## Environment Constraint
- Total physical memory: ~13.6 GB
- Observed available memory during dependency install: ~1.8 GB at one point (Windows Git Bash, `free` unavailable -- not a Linux shell)
- One `pip install` attempt hit `MemoryError` during wheel-hash verification; retried with `--no-cache-dir` and succeeded cleanly (175 packages, `pip check` clean, no broken requirements)
- Root cause was transient memory pressure during install, not a persistent block; did not require stopping Docker or WSL
- Decision: user opted to use **Podman** instead of Docker Compose for the eventual production/deployment target, to reduce RAM overhead. This is a planned deviation from the master blueprint's Docker-only wording (sections 7.3, 32) -- to be implemented at Phase 12 (Docker/Podman production readiness). Podman Compose is expected to consume the existing `docker-compose.yml` with minimal changes; not yet attempted.
- No global Python packages were removed or modified this session.

## Database and Migration State
| Migration | Applied | Verified | Notes |
|---|---|---|---|
| N/A -- no Alembic | N/A | `Base.metadata.create_all` for new tables (session.py) | Still true for brand-new tables. |
| Phase 1: add customer/jurisdiction/business_unit tables + org_profile columns | Yes, on live Postgres and SQLite fallback | Yes -- schema inspected via psql, 3 existing org_profile rows preserved with original UUIDs, is_active retrofitted to true (not NULL) | Applied via new `_add_missing_columns()` helper in session.py, runs automatically in `init_db()` on every app startup |
| Phase 1: seed_defaults() -- Tata Group customer, 7 jurisdictions, org_profile backfill | Yes, on live Postgres and SQLite fallback | Yes -- psql query confirms all 3 org_profile rows joined to TATA_GROUP customer | Idempotent get-or-create, tested for double-run and for not clobbering an already-scoped profile |

Existing tables (all confirmed in src/legal_graphrag/db/models.py): org_profile, document, clause, risk_flag, playbook_entry, review_action, audit_log, summary_version, knowledge_reference.
New tables (Phase 1): customer, jurisdiction, business_unit (schema-only, no rows yet).

## API State
| Route | Existing/New | Status | Auth | Test |
|---|---|---|---|---|
| POST /api/auth/login | Existing | Working | rate-limited | not directly tested this session |
| POST /api/auth/logout | Existing | Working | session | |
| GET /api/auth/me | Existing | Working | session required | |
| POST /api/ingestion/jobs | Existing | Working | session + rate limit | covered by test_integration_ingestion.py |
| POST /api/ingestion/jobs/{id}/resume | Existing | Working | session + rate limit | covered, 1 of 2 failures here (Neo4j DNS) |
| GET /api/ingestion/jobs/{id} | Existing | Working | session | |
| POST /api/query/jobs, resume, get | Existing | Working (single-shot Q&A) | session + rate limit | not multi-turn |
| GET /api/audit/{job_id} | Existing | Working | session | 1 of 2 failures here (404 case, Neo4j DNS) |
| GET /api/dashboard/stats | Existing | Working | session | |
| GET /api/org-profiles | Existing | Working | session | |
| GET /api/documents/{id} | Existing | Working | session | |
| POST /api/review-actions | Existing | Working | session | |
| GET/POST /api/documents/{id}/summary | Existing | Working | session | |
| GET /api/portfolio/conflicts | Existing | Working | session | |
| GET /api/eval/summary, POST /api/eval/refresh | Existing (uncommitted) | Working per code review | session | not yet independently tested |
| GET /health | Existing | Working | none | |
| Confidentiality classify/override endpoints | Missing | -- | -- | blueprint section 19 |
| Decision Brief / approval-chain endpoints | Missing | -- | -- | blueprint section 19 |
| Standards resolution endpoint | Missing | -- | -- | blueprint section 19 |

## Frontend State
| Surface | Status | Document-context aware | Test |
|---|---|---|---|
| Upload/ingestion form | Present | Yes (setCurrentDocument on response) | manual only |
| Ask/Q&A panel | Present, single-shot | Partial (auto-fills collection, editable) | manual only |
| Document Detail view | Present | Yes | manual only |
| Portfolio conflicts | Present | Partial | manual only |
| Audit lookup | Present | Yes (auto-fills job ID) | manual only |
| Eval/Stats panel | Present, minimal (3 metrics) | N/A | manual only |
| Decision Brief / Approval UI | Missing | -- | -- |
| Standards administration UI | Missing | -- | -- |
| Signed-in profile dropdown | Not yet inspected in detail | -- | -- |

## AI Evaluation State
| Evaluation | Dataset Size | Latest Score | Baseline/Current | Last Run |
|---|---:|---:|---|---|
| Eval cache (clause_recall/risk_precision/missing_clause_accuracy) | unknown, see tests/eval/labeled_eval_set.json | not run this session | -- | Not evaluated this session (requires live GEMINI_API_KEY, skipped) |
| RAGAS-based RAG/chat eval | 0 | Not evaluated | -- | Not implemented yet (blueprint section 24) |
| Clause extraction precision/recall/F1 by type | 0 | Not evaluated | -- | Not implemented yet (blueprint section 21) |
| Confidentiality classification metrics | 0 | Not evaluated | -- | Not implemented yet (blueprint section 23) |

## Known Gaps
- [x] Multi-tenancy model -- Customer/Jurisdiction/BusinessUnit added, org_profile backfilled (Phase 1). DocumentType table still missing. Query-layer isolation (RLS/auth-scoping) still missing -- see Phase 1 Decisions.
- [ ] Automatic hybrid confidentiality classification + override audit -- section 12
- [ ] Deterministic standards-resolution service (8-level hierarchy) -- section 11
- [ ] Decision Brief generation + approval chain -- section 15
- [ ] Multi-turn per-document chat (current Ask is single-shot) -- section 17.5
- [ ] Read-only document metadata in tabs (currently editable-but-prefilled) -- section 17.2
- [ ] Standards administration UI -- section 18
- [ ] Full evaluation framework: clause/risk/confidentiality/retrieval/RAGAS metrics -- sections 20-28
- [ ] Swagger docs not gated by environment -- section 6.1
- [ ] Non-root Docker user, api healthcheck -- section 32
- [ ] Podman migration for deployment target (user-requested deviation from Docker-only wording)

## Next Exact Actions
1. Commit Phase 1 (tenancy models + session.py migration/seed helpers + tests) as a clean phase commit, pending user confirmation to commit.
2. Begin Phase 2 (Auth, profile, and access control): gate Swagger/docs by environment, centralize permission checks, add negative authorization tests.
3. Decide whether Phase 2 or Phase 5 is where org-to-user access scoping first gets wired in (currently no user-to-organization mapping exists at all -- the reviewer roster has roles but no org assignment).

## Commands That Last Passed
```bash
cd D:\LegalAssistant
.venv/Scripts/python.exe -m pip check   # clean, 175 packages
.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"  # 2.13.0+cpu, False
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/eval --ignore=tests/test_integration_ingestion.py   # 24 passed
docker compose build api && docker compose up -d api   # rebuilds image with schema changes, seed_defaults() runs on startup
docker ps -a   # postgres, neo4j, minio, api all healthy/running
```
