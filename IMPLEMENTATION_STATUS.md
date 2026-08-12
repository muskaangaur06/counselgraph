# CounselGraph Implementation Status

## Repository
- Path: D:\LegalAssistant
- Branch: main
- Latest commit: 3ff2821 (Add auth and profile menu), 4 commits ahead of origin/main, not pushed. Phase 3 changes below not committed yet.
- Working tree: modified, see Phase 3 files below
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
- Phase: 3 (Document-centric frontend) -- COMPLETE
- Objective: make the selected document persist across refresh, add a universal document header, convert auto-filled-but-editable inputs to read-only, prevent stale results from a previous document lingering after switching
- Files changed: src/legal_graphrag/api/static/index.html only (no backend changes this phase)
- Focused tests: no new pytest tests (frontend-only change) -- verified with 3 scripted Playwright runs against the live container (see Phase 3 Decisions); full fast suite re-run for regression (31 passed, unaffected since no Python changed)
- Blockers: none

## Phase 3 Decisions
- currentDocument was previously a plain in-memory JS variable with zero persistence -- a refresh lost the selected document entirely. Fixed with a dual mechanism: the document_id is written to the URL as ?document=<id> (shareable, and the source of truth on load) and the fuller context (jobId/collectionName/docMeta) is cached in localStorage. On load, if the URL names a document, it wins over any stale cached one; if only a stored context exists (no URL), that's restored into the URL via history.replaceState.
- Added a real document header (#docHeader, filename + status badge + org profile/counterparty/type/confidentiality/job/doc-id) shown on every view, replacing the old Review-Workspace-only "Currently viewing: X" note. A "Change document" button clears it explicitly -- selecting a document is still deliberate, not automatic.
- askCollection/auditJobId/documentDetailId now go read-only (grayed, field-readonly class) once currentDocument has a value for them, and revert to editable+cleared (not just editable+stale) when the document is cleared. Verified via Playwright: value is genuinely "" after clearing, not just unlocked with old text still showing.
- No real per-document chat session exists yet to leak across documents (the "Ask" feature is still single-shot per section 17.5's known gap, tracked separately). What I did fix: clearCurrentDocument() now resets askThreadId and blanks askResult/auditResult/reviewResult, so switching documents can't leave a stale answer or audit result on screen that looks like it belongs to the newly-selected document.
- Verified end-to-end with Playwright against the live Docker container: load a document by ID -> header renders with real metadata -> switch tabs (header + read-only fields persist) -> full page reload (header/URL restore correctly, confirmed via ?document=<id> in the URL) -> Change document (header disappears, all fields and result panels reset to empty/editable).

## Phase 2 Decisions
- No public registration existed to remove (verified: no register routes/forms anywhere; "register open"/"Register of Legal Documents" in the UI is courthouse-docket theming, not a signup feature). Section 16.1 was already compliant.
- Did NOT build a general-purpose permission-mapping framework speculatively. Audited the codebase for actual role-based logic first: found exactly one real candidate -- RiskFlag.assigned_role (langgraph_agent.py's risk-based routing to senior_counsel for high-severity/low-confidence flags) was never enforced at the API layer, so any reviewer could act on a senior_counsel-routed flag. Fixed that specific gap with a small role-seniority helper (can_act_on_assigned_role in security.py) rather than inventing a broader system with no other consumer yet.
- Swagger gating: added ENVIRONMENT env var (default "development", matching .env.example's absence of it), FastAPI() now passes docs_url=None/redoc_url=None/openapi_url=None when ENVIRONMENT=="production". Verified /docs still returns 200 by default (dev) and left unset in .env/docker-compose.yml -- explicit production deployment (Phase 12) will need to set ENVIRONMENT=production.
- Profile dropdown reuses GET /api/auth/me (already returned {username, role}) and the existing POST /api/auth/logout -- no new backend endpoint needed. Verified visually with a scripted Playwright run against the live container: login -> avatar/name/role render correctly -> dropdown opens -> logout returns to the login screen.
- Discovered while testing: TestClient(app) picks up the repo's real .env, whose NEO4J_URI points at the container-only "neo4j" hostname -- any test that boots the full app without unsetting it hangs for ~700s on DNS-retry backoff (same root cause as the Phase 0 baseline failures). New tests unset NEO4J_URI and mock out preload_models() (the embedder/reranker aren't needed for auth tests, and loading them hit a Windows "paging file too small" OSError on this host).

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
| 1 | Complete | 7f39083 | 24 passed (19 pre-existing + 5 new tenancy tests), verified against live Postgres too | Customer/Jurisdiction/BusinessUnit tables added, org_profile backfilled. See Phase 1 Decisions above. |
| 2 | Complete | 3ff2821 | 31 passed (24 pre-existing + 7 new authorization tests); verified against live Docker container (rebuild + health check + /docs 200 + Playwright login/dropdown/logout screenshots) | Swagger env-gated, profile dropdown, senior_counsel routing enforced. See Phase 2 Decisions above. |
| 3 | Complete | pending (this phase not yet committed) | 31 passed (unaffected, frontend-only change); verified with 3 scripted Playwright runs against the live container | URL/localStorage document persistence, universal document header, read-only metadata fields, stale-result cleanup on document switch. See Phase 3 Decisions above. |

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
| POST /api/review-actions | Existing, enhanced | Working, now enforces role seniority vs. risk_flag.assigned_role | session + role check | tests/test_review_action_authorization.py |
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
| Universal document header | Present (Phase 3) | Yes -- filename/status/org/counterparty/type/confidentiality/job/doc-id | Playwright, manual |
| Upload/ingestion form | Present | Yes (setCurrentDocument on response) | manual only |
| Ask/Q&A panel | Present, single-shot | Yes (read-only once set, not just prefilled) | Playwright, manual |
| Document Detail view | Present | Yes | Playwright, manual |
| Portfolio conflicts | Present | Partial | manual only |
| Audit lookup | Present | Yes (read-only once set, not just prefilled) | Playwright, manual |
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
- [ ] Multi-turn per-document chat (current Ask is still single-shot per thread) -- section 17.5
- [x] Read-only document metadata in tabs -- fixed Phase 3, fields go read-only once currentDocument has a value, revert to editable+cleared on "Change document"
- [x] Document context survives refresh -- fixed Phase 3, persisted via ?document=<id> URL param + localStorage
- [x] Universal document header -- fixed Phase 3, shown on every view
- [ ] Standards administration UI -- section 18
- [ ] Full evaluation framework: clause/risk/confidentiality/retrieval/RAGAS metrics -- sections 20-28
- [x] Swagger docs not gated by environment -- fixed Phase 2, ENVIRONMENT=production disables /docs,/redoc,/openapi.json
- [x] Signed-in profile menu -- fixed Phase 2, replaces static "signed in" label + bare logout button
- [ ] User-to-organization/business-unit assignment (reviewer roster has roles but no org scope) -- needed before real cross-org access control is possible
- [ ] Non-root Docker user, api healthcheck -- section 32
- [ ] Podman migration for deployment target (user-requested deviation from Docker-only wording)

## Next Exact Actions
1. Commit Phase 3 (document header, URL/localStorage persistence, read-only fields, stale-result cleanup) as a clean phase commit, pending user confirmation to commit.
2. Begin Phase 4 (Automatic confidentiality): add the classification migration/fields, hybrid deterministic+Gemini classifier, human override + audit trail, UI badge/history.
3. User-to-organization assignment is still an open gap (reviewer roster has roles but no org scope) -- revisit when Phase 5 (tenant-filtered RAG) needs real cross-org isolation to test against, since there's still only one customer/no real cross-org scenario to enforce today.

## Commands That Last Passed
```bash
cd D:\LegalAssistant
.venv/Scripts/python.exe -m pip check   # clean, 175 packages
.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"  # 2.13.0+cpu, False
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/eval --ignore=tests/test_integration_ingestion.py   # 31 passed
docker compose build api && docker compose up -d api   # rebuilds image with schema+auth changes, seed_defaults() runs on startup
docker ps -a   # postgres, neo4j, minio, api all healthy/running
curl http://localhost:8000/docs   # 200 by default (ENVIRONMENT unset = development)
```
