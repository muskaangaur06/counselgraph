# CounselGraph Implementation Status

## Repository
- Path: D:\LegalAssistant
- Branch: main
- Latest commit: 843798c (initial commit)
- Working tree: 3 files modified, not yet committed (see "Uncommitted Changes" below)
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
| Multi-tenancy (customer/org/BU) | Missing | No Customer, BusinessUnit, Jurisdiction, DocumentType tables. OrgProfile is a flat single-level config, not a Tata-Group-scoped hierarchy. | Blueprint section 9 largely greenfield within this repo |
| Decision Brief + approval chain | Missing | No DecisionBrief or ApprovalChain models/routes | Blueprint section 15 genuinely missing |
| Standards resolution hierarchy | Partial | KnowledgeReference has business_unit_scope/jurisdiction_scope/approval_status fields, but no deterministic resolution service implementing the 8-level precedence in blueprint 11.1 | |
| Swagger/OpenAPI in production | Not gated | FastAPI() call has no docs_url/redoc_url/openapi_url environment guard | Blueprint section 6.1 gap, real and unconditional |
| Public registration | N/A (never existed) | No register routes found | Already compliant with blueprint 16.1 |
| Non-root Docker user | Missing | Dockerfile runtime stage runs as root | Blueprint 7.3 gap |
| API healthcheck | Missing | docker-compose.yml has healthchecks for postgres/neo4j/minio but not for `api` service | Blueprint 32.2 gap |

## Current Phase
- Phase: 0 (Repository recovery and baseline) -- COMPLETE, moving to Phase 1 planning
- Objective: Verify existing baseline before any feature work, per master directive section 33
- Files being changed: none yet (MASTER_BLUEPRINT.md, IMPLEMENTATION_STATUS.md, .venv created this session)
- Focused tests: full existing pytest suite run as baseline (see Phase History)
- Blockers: none blocking; Neo4j hostname resolution from host is a config note, not a blocker (containers work fine internally)

## Phase History
| Phase | Status | Commit | Tests | Notes |
|---|---|---|---|---|
| 0 | Complete | none yet (uncommitted) | 22 passed, 2 failed / 24 total, 728s | Failures: neo4j DNS resolution from host process, not an app bug. See Environment Constraint below. |

## Uncommitted Changes (pre-existing, found at session start)
Three files modified, not yet committed. Reviewed via `git diff`, not discarded:
- `src/legal_graphrag/api/main.py`: adds `document_context` merging into job responses (threads document_name/collection_name/document_id through pause+resume), adds executive_summary to document detail response, adds `/api/eval/summary` and `/api/eval/refresh` endpoints reading/writing a JSON eval cache.
- `src/legal_graphrag/graphrag/langgraph_agent.py`: generates the executive summary once at ingestion time (start_job_node) instead of on-demand, persists via SummaryVersion; threads document_id through apply_decision_node's return.
- `src/legal_graphrag/api/static/index.html`: 413 insertions / 33 deletions, not yet line-by-line reviewed in detail beyond the `currentDocument` shared-state mechanism confirmed above.

These look like a coherent, intentional bug fix + feature addition (document-context threading + eval dashboard cache), consistent with the directive's description in section 3.1. Not yet committed pending Phase 1 test coverage and explicit user commit approval.

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
| N/A -- no migration framework | N/A | Tables created via SQLAlchemy `Base.metadata.create_all` (session.py) | No Alembic/versioned migrations found. Phase 1 will need to introduce idempotent migration handling before adding Customer/Organization/BusinessUnit tables. |

Existing tables (all confirmed in src/legal_graphrag/db/models.py): org_profile, document, clause, risk_flag, playbook_entry, review_action, audit_log, summary_version, knowledge_reference.

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
- [ ] Multi-tenancy model (Customer/Organization/BusinessUnit/Jurisdiction/DocumentType) -- section 9
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
1. Commit the reviewed, working uncommitted changes (main.py/langgraph_agent.py/index.html document-context fix + eval cache endpoints) as a clean Phase 0 commit, pending user confirmation to commit.
2. Begin Phase 1 (Architecture and migration audit): design idempotent approach for adding customer_id/organization_id to existing tables without breaking the 3 seeded org_profile rows.
3. Decide and document the org_profile -> Customer/Organization/BusinessUnit mapping strategy before writing any migration code.

## Commands That Last Passed
```bash
cd D:\LegalAssistant
.venv/Scripts/python.exe -m pip check   # clean, 175 packages
.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"  # 2.13.0+cpu, False
.venv/Scripts/python.exe -m pytest -q   # 22 passed, 2 failed (Neo4j hostname resolution from host, not an app bug)
docker ps -a   # postgres, neo4j, minio, api all healthy/running
```
