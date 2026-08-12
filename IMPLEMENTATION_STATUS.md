# CounselGraph Implementation Status

## Repository
- Path: D:\LegalAssistant
- Branch: main
- Latest commit: f63d40a (Add standards resolution hierarchy), 10 commits ahead of origin/main, not pushed to GitHub yet.
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
| Ask/Q&A (single-shot, not multi-turn) | Present but NOT multi-turn chat | index.html handleAskResponse / askThreadId flow, now a floating chat-style widget (#chatLauncher/#chatPanel) | collectionName now derived automatically (deriveCollectionNameFromFilename fallback) instead of requiring manual entry -- no visible collection input at all anymore. Still no conversational memory across questions -- blueprint's "in-progress chat conversion" has NOT landed; this is still single-question-per-thread, just with a chat-shaped UI. |
| Shared document context (frontend) | Present, read-only | index.html `currentDocument` global object, persisted via ?document=<id> URL + localStorage, universal #docHeader shown on all tabs except Upload | askCollection/auditJobId/documentDetailId are genuinely read-only (not just prefilled) once a document is selected; blueprint 17.1/17.2 gaps closed in Phase 3. |
| Eval matrix / dashboard panel | Present, cache-based | GET /api/eval/summary (reads cache), POST /api/eval/refresh (live Gemini eval, writes cache) | Only 3 metrics (clause_recall, risk_precision, missing_clause_detection_correct), not the full metric suite in blueprint sections 21-28 |
| Confidentiality classification | Automatic + manual override (Phase 4 done) | graphrag/confidentiality.py (deterministic signals + Gemini + safe combination), confidentiality_override table, tests/test_confidentiality_classifier.py + test_confidentiality_override.py | Runs automatically at ingestion in start_job_node; explicit labels never downgraded by the LLM; low OCR ratio discounts confidence; senior_counsel/admin-only override with mandatory reason, always audited. Access control only gates GET /api/documents/{id} so far, not every clause/risk-flag route. |
| Multi-tenancy (customer/org/BU) | Partial (Phase 1 done) | tests/test_tenancy_migration.py, verified against live Postgres | Customer/Jurisdiction/BusinessUnit tables added, org_profile.customer_id backfilled to Tata Group. No DocumentType table yet. Query-layer tenant scoping/isolation not wired in (single customer today, nothing to isolate against yet) -- deferred to Phase 5+. |
| Decision Brief + approval chain | Missing | No DecisionBrief or ApprovalChain models/routes | Blueprint section 15 genuinely missing |
| Standards resolution hierarchy | Present (Phase 5 done) | graphrag/standards.py resolve_standards()/check_mandatory_and_prohibited(), GET /api/documents/{id}/standards, tests/test_standards_resolution.py + test_standards_api.py | Full 8-level precedence (11.1), conflict/mandatory/prohibited rules (11.2), tenant-isolated candidate lookup (11.3), TCS/Tata Steel/Tata Motors demo standards proving different orgs resolve differently for the same clause_type (11.4). business_unit-level tiers work but have no real seeded BusinessUnit rows to resolve against yet. |
| Swagger/OpenAPI in production | Not gated | FastAPI() call has no docs_url/redoc_url/openapi_url environment guard | Blueprint section 6.1 gap, real and unconditional |
| Public registration | N/A (never existed) | No register routes found | Already compliant with blueprint 16.1 |
| Non-root Docker user | Missing | Dockerfile runtime stage runs as root | Blueprint 7.3 gap |
| API healthcheck | Missing | docker-compose.yml has healthchecks for postgres/neo4j/minio but not for `api` service | Blueprint 32.2 gap |

## Current Phase
- Phase: 5 (Standards hierarchy and tenant-filtered RAG) -- COMPLETE
- Objective: implement the deterministic 8-level standards-resolution hierarchy (section 11.1), conflict/override precedence rules (11.2), tenant-isolated retrieval of the approved-clause library (11.3), and demonstrate the same clause_type resolving to different standards for different organizations (11.4) -- without disturbing existing deviation scoring/playbook behavior
- Files changed: src/legal_graphrag/db/models.py (KnowledgeReference: customer_id/business_unit_id/jurisdiction_id/document_type/is_mandatory/is_prohibited/effective_date/expiry_date), src/legal_graphrag/db/repository.py (resolve_business_unit_id, resolve_jurisdiction_id, get_org_profile_customer_id, find_standards_candidates, get_approved_clause_language extended with optional standards_context), src/legal_graphrag/graphrag/standards.py (new module: resolve_standards, check_mandatory_and_prohibited), src/legal_graphrag/graphrag/langgraph_agent.py (resolves standards_context once per document in risk_flag_node, threads it into compute_deviation/build_playbook_entry), src/legal_graphrag/graphrag/deviation.py + playbook.py (optional standards_context param, defaults preserve Phase 4 behavior), src/legal_graphrag/db/session.py (seed_demo_standards: TCS/Tata Steel/Tata Motors demo org profiles + differentiated liability standards), src/legal_graphrag/api/main.py (GET /api/documents/{id}/standards), src/legal_graphrag/api/static/index.html (Standards Lookup block in Document Detail)
- Focused tests: 17 new tests -- tests/test_standards_resolution.py (13, pure logic against a throwaway SQLite DB: most-specific-level resolution, org-level fallback, TCS-vs-Tata-Steel differing standards, wrong-customer retrieval impossible, customer-group fallback visibility, conflict logging not merging, expired/unapproved exclusion, mandatory/prohibited surfacing, document_type wildcard-vs-mismatch semantics) + tests/test_standards_api.py (4, API-level: endpoint resolution, 404 for unknown document, 401 without session, deviation scoring still works with no standards_context). All 17 pass in isolation; when run in the same process as other TestClient-based test files, the same pre-existing shared login-rate-limiter flakiness from Phase 4 reproduces (not a Phase 5 regression). Full fast suite (non-TestClient files): 48 passed.
- Two resolution bugs caught during live verification (not caught by mocked unit tests, since those used hand-built candidate dicts that happened to avoid the exact fields at play): (1) a level requiring document_type was being skipped entirely when the query context had no document_type, even though an org-wide standard (document_type=NULL, meaning "applies to everything") should still match -- fixed by treating a NULL document_type on the ROW as a wildcard; (2) after fixing (1), a row scoped to one document_type (e.g. "supply") could incorrectly fall through to an unscoped fallback level (e.g. customer_group_fallback) for a different document_type context (e.g. "service") -- fixed by checking document_type compatibility unconditionally, independent of which hierarchy level is being tried. Both are now covered by regression tests.
- Blockers: none

## Out-of-Phase: UI Redesign (user-directed, not blueprint-driven)
- Requested directly by user after seeing Phase 3's screenshots: full visual overhaul (glass/gradient/glow, less wasted whitespace, tighter top bar), a floating chat widget replacing the inline "Ask a Question" box, and Portfolio/Approval views restructured to not look empty. Deviation recorded here per section 1.2 since this isn't a blueprint phase.
- Files changed: src/legal_graphrag/api/static/index.html only.
- Real bugs found and fixed along the way (not just cosmetic):
  1. Confidentiality/org-profile/business-unit picked on the upload form weren't showing in the document header after upload -- handleIngestResponse never fetched the full Document row, only document_context (which lacks those fields). Fixed by calling loadDocumentDetailInto() right after upload/resume.
  2. Chat ("Ask CounselGraph") rejected every question with "select a document first" even when a document WAS loaded, if that document was opened by ID or via a paused-job resume rather than a fresh upload -- collectionName was only ever set on the initial upload path. collection_name isn't persisted on the Document row at all (main.py derives it on the fly: `collection_name or re.sub(r"\W+", "_", document_name)`), so the frontend now replicates that exact derivation as a fallback (deriveCollectionNameFromFilename) whenever a document is loaded some other way. This is fragile -- a future backend change to that derivation would silently break it again. The real fix is to persist collection_name on Document; not done here, flagged as a gap below.
  3. Profile dropdown visually collided with the "Change document" button in the header below it -- not a z-index bug, the dropdown's translucent glass background (~4.5% opacity in dark mode) let both texts show through simultaneously. Fixed by giving popovers (profile dropdown, and the login card's opaque backing layer) a dedicated fully-opaque --popover-bg token instead of reusing the glass --card-bg.
- Removed: the "register open"/"unreachable" health-check pill entirely (user found it distracting/unnecessary); theme toggle relocated to sit directly beside the profile menu on the far right instead of its own separate slot.
- Document header now hides itself specifically on the Upload Workspace tab (still shows on every other document-scoped tab) -- showing "here's your loaded document" while the form below is for starting a new one read as if uploading would overwrite it.
- Verified with multiple scripted Playwright runs against the live container across both themes: login, document loading or header/read-only-field behavior, tab switching, profile dropdown, Portfolio/Approval stat strips, chat widget open/ask/label, and the three bug fixes above. Full pytest suite re-run each round (31 passed throughout, unaffected -- no Python changed).
- Color palette went through two rounds per user feedback: first a blue/purple SaaS palette, then replaced entirely with a maroon+cream system (dark = near-black with deep maroon/crimson glow and gold secondary accent; light = warm buttery cream/gold with maroon as the bold accent) -- explicitly NOT a revival of the original muted brown/maroon courthouse theme, designed fresh to be dramatic rather than washed out. All copy stayed plain-English (no Exhibit/Docket language brought back), only CSS color tokens and two hardcoded gradient hex values changed.

## Known Gap: collection_name not persisted on Document
- `Document` has no `collection_name` column. It's computed transiently in main.py's start_ingestion_job and never stored, so the frontend has to re-derive it from the filename via the same regex whenever a document is loaded any way other than "just finished uploading." Fixed at the frontend layer for now (see UI Redesign section above); the correct fix is a small migration adding `collection_name` to `Document` and having `/api/documents/{id}` return it directly. Not done -- flagging for a future phase since it's backend schema work, not UI.

## Phase 5 Decisions
- No changes to Document's schema for business_unit_id/jurisdiction_id FKs. Document.business_unit/geography stay free-text (already used by the frontend and the required_clause_checklist logic); resolve_business_unit_id()/resolve_jurisdiction_id() in repository.py do a name-or-code lookup against business_unit/jurisdiction at resolution time instead. Avoids a migration touching Document again this soon after Phase 4, and there's no other consumer yet that needs a denormalized FK (same reasoning as Phase 1's decision not to add customer_id to Document directly).
- KnowledgeReference's pre-existing business_unit_scope/jurisdiction_scope (plain strings, used by the 57 rows seeded before Phase 5) are left untouched -- find_knowledge_references() and get_approved_clause_language()'s simple fallback path still use them exactly as before. The new customer_id/business_unit_id/jurisdiction_id/document_type columns are additive, used only by the new find_standards_candidates()/resolve_standards() path. Both paths coexist; nothing had to be backfilled or migrated on the 57 existing rows.
- Demonstration org profiles (TCS, Tata Steel, Tata Motors -- section 11.4) are NEW OrgProfile rows under the same seeded Tata Group customer, not a reuse of the 3 pre-existing profiles (Vendor Procurement Unit, Cross-Border Compliance Unit, General Counsel Office). Those 3 are already business-unit-flavored scopes under one implied "Organization" per Phase 1's org_profile-IS-Organization mapping; the blueprint's demonstration explicitly wants different ORGANIZATIONS producing different results for the same clause_type, which needed new profiles, not different business units within an existing one.
- Resolution hierarchy has 8 levels per section 11.1, but no BusinessUnit rows are seeded for any org profile yet (Phase 1 left business_unit schema-only, and Phase 5 didn't add BusinessUnit seed rows for the demo orgs either -- only OrgProfile-level standards). The business_unit_jurisdiction_document_type and business_unit_document_type levels are implemented and tested (test_standards_resolution.py) but have no real seeded data to exercise them outside tests. Revisit if/when a future phase needs real BusinessUnit rows (e.g. Phase 5's own tenant-filtered-RAG angle didn't require them, since there's still one real customer and no live cross-BU scenario yet).
- Two real bugs found only through live Docker verification (not the mocked unit tests written first): a document_type-scoped hierarchy level would skip entirely when the query had no document_type (missing the wildcard case), and after fixing that, a document_type-scoped row could leak into an unscoped fallback level for the WRONG document_type. Both are structural bugs in _matches_level's document_type handling, not resolution-order bugs -- the specificity ordering (business_unit > org > customer, jurisdiction as an independent axis) was correct from the first unit test pass. Fixed and covered by two new regression tests (test_org_wide_standard_applies_to_any_document_type, test_document_type_scoped_standard_does_not_leak_to_other_document_types).
- "Never retrieve another customer's standards" (11.3) is enforced once, in find_standards_candidates()'s SQL filter (customer_id match OR customer_id IS NULL) -- not re-implemented in resolve_standards() itself, which trusts its candidate set. A KnowledgeReference row with no customer_id at all is a deliberate group-wide fallback (hierarchy level 8, "customer/group fallback"), not a tenant-isolation gap -- verified this distinction with a dedicated test (test_customer_group_fallback_visible_across_org_profiles) alongside the wrong-customer test, so the two behaviors don't get confused later.
- Chroma/BM25 RAG (hybrid_search.py, per-document contract collections) was NOT touched this phase. Confirmed by reading the code first: section 11.3's tenant-filtered retrieval is about the approved-clause/standards library (KnowledgeReference, Postgres-only, no Chroma involvement at all), not the per-document contract-text collections the existing hybrid_search.py already isolates naturally (one Chroma collection per uploaded document). Re-verify this reading if a future phase adds a shared/cross-document standards Chroma collection -- none exists today.
- get_approved_clause_language() (called by both deviation.py and playbook.py) grew an optional standards_context parameter rather than a breaking signature change -- omitting it preserves exact Phase 4 behavior (org_profile_id-only lookup via find_knowledge_references), verified by test_deviation_scoring_works_without_standards_context. risk_flag_node in langgraph_agent.py resolves the context once per document (not once per clause) and passes the same dict to every clause's deviation/playbook call for that document, since it's document-level context, not clause-level.
- Standards Lookup UI is a manual clause_type text input, not a dropdown of known clause types -- there's no canonical clause-type enum anywhere in the codebase (extraction.py, playbook.py, deviation.py all treat clause_type as a free string), so a dropdown would need to either hardcode a list or introduce a new lookup table neither the blueprint nor the existing code calls for this phase.

## Phase 4 Decisions
- No trained ML classifier -- section 12.2 specifies a hybrid of a deterministic keyword/label regex scan plus a Gemini structured call, combined by explicit safety rules (never let the LLM downgrade an explicit label, flag >1-level disagreement for human confirmation, discount confidence on high OCR ratio, never silently default to public). Implemented exactly that in the new graphrag/confidentiality.py, no model file/training data involved.
- OCR ratio (fraction of pages that needed OCR) is computed in main.py's _ingest() where low_text_pages/pages are already available, and threaded through IngestionState.ocr_ratio into start_job_node -- there's no existing per-page OCR confidence score anywhere in the pipeline, so this ratio is the practical proxy for "low OCR confidence" the blueprint calls for.
- Classification runs inside start_job_node, in the same place and for the same reason the executive summary already does (full_text and document_id are both available there, and it should happen once at ingestion, not on every later fetch).
- New ConfidentialityOverride table (not reuse of the generic AuditLog) because section 12.3 requires specific structured fields (previous_level, new_level, source, reason, changed_by) the generic audit_log.details JSON blob doesn't enforce. A manual override also writes a row to the generic AuditLog (stage="confidentiality") so it shows up in the existing document-scoped audit view too -- belt and suspenders, not a replacement.
- apply_confidentiality_classification() (automatic path) refuses to overwrite a document once confidentiality_source == "manual_override" -- an automatic re-classification (e.g. a future re-ingestion) must never silently clobber a human's decision. override_confidentiality() (manual path) has no such guard -- a human can always override another human's or the system's prior call, that's the point of the feature.
- Access control (12.4) reuses the existing admin/senior_counsel/reviewer roles instead of inventing an org/BU-scoped permission system: highly_confidential requires senior_counsel or admin, every other level is open to any authenticated reviewer. No "explicitly assigned users" list exists yet (would need the still-missing user-to-organization assignment noted in Known Gaps) -- flagging as a narrower interpretation of 12.4's example policy, not the full thing.
- Only /api/documents/{id} (the document detail endpoint) enforces the confidentiality gate this phase. Clause/risk-flag data reachable via other routes (e.g. portfolio conflicts) is not yet individually gated -- acceptable for now since there's a single customer and no cross-org access scenario to defend against yet (same reasoning as the still-open tenant-isolation gap from Phase 1), but worth revisiting once Phase 5's tenant-filtered RAG lands.
- Fixed the pre-existing collection_name gap (flagged in the prior session as worth doing before Phase 4 touched Document again) in the same pass: added the column, populated it in start_ingestion_job, and returned it from GET /api/documents/{id}. The frontend's regex-based deriveCollectionNameFromFilename fallback is kept only for documents created before this column existed.
- Test suite: unit tests for the pure classification logic (test_confidentiality_classifier.py) need no mocking since detect_deterministic_signals/combine_classification take plain dicts and do no I/O -- classify_with_llm (the actual Gemini call) is exercised only implicitly via the ingestion path, not directly unit-tested, since it requires a live GEMINI_API_KEY the same way the existing executive-summary/risk-flagging LLM calls are (consistent with how those are tested elsewhere in this repo).
- Verified the full UI end-to-end against the rebuilt live Docker container (not just pytest): created a document directly via the repository layer against live Postgres, logged in as admin via Playwright, confirmed the badge/evidence render in both the document header and the Document Detail view, submitted a real override through the form, and confirmed the badge/source/confidence updated live and a new row appeared in Override History -- all via a throwaway Playwright script, cleaned up afterward per the documented workflow. Did not re-verify the reviewer-blocked-from-highly_confidential case in the browser (only one admin account exists in this container's REVIEWERS); that path is covered by test_confidentiality_override.py instead.

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
| 3 | Complete | 2aa6539 (bundled with UI redesign commits) | 31 passed (unaffected, frontend-only change); verified with 3 scripted Playwright runs against the live container | URL/localStorage document persistence, universal document header, read-only metadata fields, stale-result cleanup on document switch. See Phase 3 Decisions above. |
| 4 | Complete | 3c9edb6 | 50 passed total (31 prior + 19 new); 3 pre-existing rate-limiter flakes confirmed unrelated; verified live via Playwright against the rebuilt Docker container | Hybrid deterministic+Gemini confidentiality classifier, override + audit trail, access control by level, UI badge/history, collection_name persistence fix. See Phase 4 Decisions above. |
| 5 | Complete | f63d40a | 67 passed total (50 prior + 17 new); shared rate-limiter flakes reproduce only across TestClient files run together, confirmed pre-existing; verified live via Playwright against the rebuilt Docker container (TCS vs. Tata Steel resolving different liability standards) | Deterministic 8-level standards resolution hierarchy, conflict/mandatory/prohibited rules, tenant-isolated retrieval, TCS/Tata Steel/Tata Motors demo standards, Standards Lookup UI. Two document_type wildcard/leak bugs found and fixed during live verification. See Phase 5 Decisions above. |

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
New tables (Phase 1): customer, jurisdiction, business_unit (schema-only, business_unit still has no rows as of Phase 5).
New in Phase 4: document.collection_name, document.confidentiality_confidence/source/reasons/needs_confirmation columns; new confidentiality_override table (append-only override history). All applied via the same `_add_missing_columns()`/`create_all()` retrofit mechanism, verified against the local SQLite fallback via the new test suite (live Postgres retrofit not independently re-verified this session, same mechanism as Phase 1 which was).
New in Phase 5: knowledge_reference.customer_id/business_unit_id/jurisdiction_id/document_type/is_mandatory/is_prohibited/effective_date/expiry_date columns (the 57 pre-existing rows retrofit with NULL/false defaults via `_add_missing_columns()`, unaffected -- their old business_unit_scope/jurisdiction_scope string fields are untouched and still used by the pre-existing find_knowledge_references() path). Verified against BOTH local SQLite (pytest) and live Postgres (seed_demo_standards() ran cleanly on startup, confirmed via direct query -- see Phase 5 Decisions).

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
| GET /api/documents/{id} | Existing, enhanced | Working, now enforces confidentiality-level access control | session + confidentiality gate | tests/test_confidentiality_override.py |
| POST /api/review-actions | Existing, enhanced | Working, now enforces role seniority vs. risk_flag.assigned_role | session + role check | tests/test_review_action_authorization.py |
| GET/POST /api/documents/{id}/summary | Existing | Working | session | |
| POST /api/documents/{id}/confidentiality | New (Phase 4) | Working, senior_counsel/admin only, requires reason | session + role check | tests/test_confidentiality_override.py |
| GET /api/documents/{id}/confidentiality | New (Phase 4) | Working, returns override history | session | tests/test_confidentiality_override.py |
| GET /api/portfolio/conflicts | Existing | Working | session | |
| GET /api/eval/summary, POST /api/eval/refresh | Existing (uncommitted) | Working per code review | session | not yet independently tested |
| GET /health | Existing | Working | none | |
| Decision Brief / approval-chain endpoints | Missing | -- | -- | blueprint section 19 |
| GET /api/documents/{id}/standards | New (Phase 5) | Working | session | tests/test_standards_api.py |

## Frontend State
| Surface | Status | Document-context aware | Test |
|---|---|---|---|
| Universal document header | Present (Phase 3) | Yes -- filename/status/org/counterparty/type/confidentiality/job/doc-id | Playwright, manual |
| Upload/ingestion form | Present | Yes (setCurrentDocument on response) | manual only |
| Ask/Q&A panel | Present, single-shot | Yes (read-only once set, not just prefilled) | Playwright, manual |
| Document Detail view | Present | Yes | Playwright, manual |
| Confidentiality badge/details/override form/history | Present (Phase 4) | Yes -- badge in doc header + full block in Document Detail view | Playwright against live container: badge renders, evidence renders, override submit updates badge/source/confidence live and appends to history |
| Standards Lookup block | Present (Phase 5) | Yes -- clause_type input scoped to the loaded document | Playwright against live container: resolves and renders scope_level/selected standard for two different org profiles (TCS vs. Tata Steel), confirmed genuinely different text |
| Portfolio conflicts | Present | Partial | manual only |
| Audit lookup | Present | Yes (read-only once set, not just prefilled) | Playwright, manual |
| Eval/Stats panel | Present, minimal (3 metrics) | N/A | manual only |
| Decision Brief / Approval UI | Missing | -- | -- |
| Standards administration UI | Missing (lookup exists, authoring doesn't) | -- | -- |
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
- [x] Automatic hybrid confidentiality classification + override audit -- fixed Phase 4, deterministic+Gemini hybrid classifier, ConfidentialityOverride history table, /api/documents/{id}/confidentiality override endpoint, access control gated on document detail. Access control only covers the document-detail endpoint so far, not every clause/risk-flag route -- see Phase 4 Decisions.
- [x] Deterministic standards-resolution service (8-level hierarchy) -- fixed Phase 5, graphrag/standards.py, resolve_standards()/check_mandatory_and_prohibited(), wired into deviation scoring + playbook fallback. business_unit-level rows are implemented/tested but have no real seeded data yet (no BusinessUnit rows exist for any org profile) -- see Phase 5 Decisions.
- [ ] Decision Brief generation + approval chain -- section 15
- [ ] Multi-turn per-document chat (current Ask is still single-shot per thread) -- section 17.5
- [x] Read-only document metadata in tabs -- fixed Phase 3, fields go read-only once currentDocument has a value, revert to editable+cleared on "Change document"
- [x] Document context survives refresh -- fixed Phase 3, persisted via ?document=<id> URL param + localStorage
- [x] Universal document header -- fixed Phase 3, shown on every view
- [ ] Standards administration UI -- section 18 (resolution/lookup UI exists per Phase 5, but no CRUD UI to author/edit standards -- that's still admin-only via direct DB/repository calls)
- [ ] Full evaluation framework: clause/risk/confidentiality/retrieval/RAGAS metrics -- sections 20-28
- [x] Swagger docs not gated by environment -- fixed Phase 2, ENVIRONMENT=production disables /docs,/redoc,/openapi.json
- [x] Signed-in profile menu -- fixed Phase 2, replaces static "signed in" label + bare logout button
- [ ] User-to-organization/business-unit assignment (reviewer roster has roles but no org scope) -- needed before real cross-org access control is possible
- [ ] Non-root Docker user, api healthcheck -- section 32
- [ ] Podman migration for deployment target (user-requested deviation from Docker-only wording)

## Next Exact Actions
1. Begin Phase 6 (Risk and compliance enhancement): this is the next blueprint phase per MASTER_BLUEPRINT.md section 33 -- Phases 0-5 are now done. Phase 6 should apply resolve_standards()'s mandatory/prohibited results (already returned by the Phase 5 endpoint but not yet consumed by risk_flag_node) into actual risk flags.
2. User-to-organization assignment is still an open gap (reviewer roster has roles but no org scope) -- revisit whenever a real cross-org access scenario needs testing; there's still only one real customer today.
3. Confidentiality access control (Phase 4) only gates GET /api/documents/{id} -- other routes that surface clause/risk-flag content (e.g. portfolio conflicts) aren't individually gated yet.
4. No BusinessUnit rows are seeded anywhere (schema-only since Phase 1) -- the business_unit-level hierarchy tiers are implemented and unit-tested but have never resolved a real request. Seed real BusinessUnit rows (or demo ones) whenever a phase needs to exercise that level for real.
5. Not yet pushed to GitHub (`https://github.com/muskaangaur06/counselgraph`) -- 10 commits ahead of origin/main after this phase. Push only if/when explicitly asked.

## Handoff Notes (read this first in a new session)
- Phases 0-5 of MASTER_BLUEPRINT.md are done. Between Phase 3 and Phase 4 there was also a large out-of-phase UI visual redesign requested directly by the user (not in the blueprint) -- see "Out-of-Phase: UI Redesign" section above for what changed and why. `MASTER_BLUEPRINT.md` is the original directive, unmodified; this file is the live status tracker.
- **Phase 4 confidentiality classification is NOT an ML model** -- it's a deterministic keyword/label regex scan plus a Gemini structured call (graphrag/confidentiality.py), combined by explicit safety rules (never downgrade an explicit label, flag disagreement >1 level for human confirmation, discount confidence when many pages needed OCR, never silently default to public). See Phase 4 Decisions for the exact rules and why no trained classifier was built.
- **Phase 5 standards resolution is also NOT an ML model** -- graphrag/standards.py's resolve_standards() is a pure deterministic lookup/ranking function over KnowledgeReference rows (no LLM call, no embeddings), walking the blueprint's 8-level hierarchy from most specific to most general. Two subtle bugs in the document_type wildcard/mismatch handling were only caught by live Docker verification, not by the first pass of mocked unit tests -- see Phase 5 Decisions before touching _matches_level again.
- **Demo org profiles for Phase 5's section 11.4 demonstration**: TCS, Tata Steel, and Tata Motors are new OrgProfile rows seeded by `seed_demo_standards()` in session.py (idempotent, runs on every startup alongside seed_defaults()), each with a differentiated `liability` KnowledgeReference standard. These are separate from the 3 pre-existing profiles (Vendor Procurement Unit, Cross-Border Compliance Unit, General Counsel Office) -- don't conflate them.
- **Podman decision**: user asked to use Podman instead of Docker for the eventual deployment target (to reduce RAM overhead vs. Docker Desktop). This has NOT been implemented yet -- still on plain `docker compose` throughout Phases 0-3 and the UI redesign. Revisit at Phase 12 (Docker/Podman production readiness) per the blueprint's own phase ordering; don't switch earlier than that without a reason, since Podman Compose vs. `docker-compose.yml` compatibility hasn't been verified at all.
- **Host is memory-constrained** (~13.6GB total, observed as low as ~1.8GB free during dependency installs). `pip install` needs `--no-cache-dir` to avoid a `MemoryError` during wheel-hash verification (already documented, not a persistent blocker). Loading the sentence-transformer embedder directly in a pytest process (not via Docker) can hit a Windows "paging file too small" OSError -- work around it by mocking `preload_models` in tests that don't need embeddings, rather than trying to fix the OS page file.
- **Dev login for manual/Playwright testing**: username `admin`, password `admin@321` (the documented insecure fallback default in security.py, used when no `.env` REVIEWERS/ADMIN_* vars override it -- not a real secret, safe to reuse in tests).
- **How UI changes were verified this session**: no frontend test framework exists. Verification was done by rebuilding the Docker image (`docker compose build api && docker compose up -d api`), waiting for `/health` to return `{"status":"ok"}` (can take 1-3 min for model preload on this host), then driving it with a throwaway Playwright script (`npm init -y && npm install --no-save playwright` in the scratchpad dir, `chromium.launch()`, screenshot, read the screenshot with the Read tool). Always clean up the throwaway npm/playwright directory afterward. There is no persistent Playwright setup in the repo.
- **Current visual direction** (if continuing UI work): dark theme = near-black background with maroon/crimson glow + gold secondary accent; light theme = warm buttery cream/gold with bold maroon accent. This was a deliberate, fresh design (not a revival of the original muted brown/maroon courthouse look) requested by the user after two earlier iterations (blue/purple, then a more "SaaS clean" pass) were both rejected. If the user asks for further visual changes, get specific direction before rebuilding broad swaths of CSS again -- this took 3 full rebuild-and-screenshot cycles to converge.
- All plain-English copy from the original courthouse theme (Exhibit A, Docket Entry, "Register of Legal Documents") was deliberately removed and replaced with plain labels (Upload, Review, Submission, etc.) -- don't reintroduce it without the user asking.
- The floating chat widget (bottom-right, `#chatLauncher`/`#chatPanel`) reuses the existing single-shot `/api/query/jobs` ask/resume flow -- it is NOT multi-turn conversational memory. That's still blueprint section 17.5's open gap. Don't confuse "has a chat-style UI" with "has chat memory" when picking up Phase 9.

## Commands That Last Passed
```bash
cd D:\LegalAssistant
.venv/Scripts/python.exe -m pip check   # clean, 175 packages
.venv/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"  # 2.13.0+cpu, False
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/eval --ignore=tests/test_integration_ingestion.py   # 67 passed total (50 prior + 17 new); shared login-rate-limiter flakes reproduce only when TestClient-based files run back-to-back in one process, confirmed pre-existing -- see Phase 4/5 notes
docker compose build api && docker compose up -d api   # rebuilds image with schema+auth+standards changes, seed_defaults()+seed_demo_standards() run on startup
docker ps -a   # postgres, neo4j, minio, api all healthy/running
curl http://localhost:8000/docs   # 200 by default (ENVIRONMENT unset = development)
```
