# CLAUDE CODE MASTER IMPLEMENTATION DIRECTIVE  
# CounselGraph — AI Legal Document Intelligence System

You are continuing development of the existing CounselGraph repository.

Repository:
- Local path: `D:\LegalAssistant`
- GitHub: `https://github.com/muskaangaur06/counselgraph`
- Product: CounselGraph — AI Legal Document Intelligence System
- Current AI provider: Gemini API
- Current architecture: FastAPI application, PostgreSQL, Neo4j, MinIO, Chroma, LangGraph, Docker Compose
- Current frontend may be served by the FastAPI application and may use existing HTML/CSS/JavaScript rather than a separate Next.js application. Preserve the working frontend architecture unless inspection proves that a migration is necessary.

Your task is to inspect, enhance, test, document, and Dockerize the existing project according to this master blueprint.

This is an incremental enhancement project, not a greenfield rewrite.

---

# 1. NON-NEGOTIABLE WORKING RULES

## 1.1 Inspect before changing

Before implementing any feature:

1. Inspect the existing repository.
2. Inspect `git status`.
3. Inspect the current database models and migrations.
4. Inspect the current API routes.
5. Inspect the existing frontend pages, tabs, components, and shared state.
6. Inspect the existing Docker configuration.
7. Inspect existing tests.
8. Inspect the in-progress conversational chat implementation.
9. Identify what is:
   - already implemented and aligned;
   - partially implemented;
   - implemented but misaligned;
   - genuinely missing.

Do not recreate working features.

Do not replace working code merely because another framework or pattern is preferred.

Do not create duplicate services, duplicate models, duplicate routes, duplicate tabs, duplicate database tables, or duplicate frontend components.

## 1.2 Reuse and enhancement policy

For every requirement:

- If it already exists and aligns with this blueprint, preserve it.
- If it exists but is incomplete, enhance it.
- If it exists but conflicts with the blueprint, refactor it with the smallest safe change.
- Overwrite existing code only when it is unusable, unsafe, redundant, or clearly misaligned.
- Before replacing a major implementation, record the reason in `IMPLEMENTATION_STATUS.md`.
- Do not rewrite the complete application.
- Do not migrate the frontend to another framework unless there is a demonstrated technical necessity and the change is approved in the status plan.

## 1.3 Token-efficiency rules

Use a low-redundancy workflow suitable for Sonnet with medium effort:

1. Read only the files relevant to the current phase.
2. Use targeted searches instead of reading the whole repository repeatedly.
3. Keep a persistent project-state file so later phases do not rediscover work.
4. Make small, coherent changes.
5. Run the smallest relevant tests first.
6. Fix failures immediately within the same phase.
7. Run the full regression suite only at planned checkpoints.
8. Do not repeatedly print large files.
9. Do not regenerate documentation after every small edit.
10. Do not produce long explanations during implementation; update status files instead.
11. Do not use speculative APIs, table names, or file paths without inspecting the project first.
12. Do not add placeholder implementations using `pass`, fake success responses, or hardcoded evaluation scores.
13. Do not claim features work until they have been tested.
14. Do not claim metrics unless they were calculated from a labeled dataset.
15. When blocked, state the exact blocker and the smallest required decision.

## 1.4 Phase completion protocol

Each phase must follow this sequence:

1. Assess current implementation.
2. Record findings in `IMPLEMENTATION_STATUS.md`.
3. Implement only missing or misaligned requirements.
4. Add or update focused tests.
5. Run focused tests.
6. Debug until focused tests pass.
7. Run the relevant regression tests for affected existing features.
8. Update `IMPLEMENTATION_STATUS.md` with:
   - files changed;
   - database changes;
   - routes changed;
   - tests run;
   - test results;
   - unresolved issues;
   - next phase.
9. Make one clean phase commit only after tests pass.
10. Do not push automatically unless explicitly instructed, but prepare the repository for a push.

Do not move to the next phase with unexplained failed tests.

If a test is obsolete because the intended behavior changed, update the test and record why. Do not simply delete tests to make the suite pass.

---

# 2. REQUIRED PROJECT CONTROL FILES

Create these two files at the repository root before feature work begins.

## 2.1 `MASTER_BLUEPRINT.md`

Copy the substantive architecture, workflow, requirements, phases, testing strategy, evaluation strategy, and completion criteria from this directive into `MASTER_BLUEPRINT.md`.

This is the authoritative product and implementation blueprint.

Do not modify it casually. If implementation realities require a deviation, record the deviation in `IMPLEMENTATION_STATUS.md` first.

## 2.2 `IMPLEMENTATION_STATUS.md`

Create a concise, token-optimized state tracker with the following structure:

```md
# CounselGraph Implementation Status

## Repository
- Path:
- Branch:
- Latest commit:
- Working tree:
- Last updated:

## Environment
- Python:
- Virtual environment:
- Docker Compose:
- Database:
- Neo4j:
- Chroma:
- MinIO:
- Gemini:
- Frontend delivery model:

## Current Verified Capabilities
| Capability | Status | Evidence/Test | Notes |
|---|---|---|---|

## Current Phase
- Phase:
- Objective:
- Files being changed:
- Focused tests:
- Blockers:

## Phase History
| Phase | Status | Commit | Tests | Notes |
|---|---|---|---|---|

## Database and Migration State
| Migration | Applied | Verified | Notes |
|---|---|---|---|

## API State
| Route | Existing/New | Status | Auth | Test |
|---|---|---|---|---|

## Frontend State
| Surface | Status | Document-context aware | Test |
|---|---|---|---|

## AI Evaluation State
| Evaluation | Dataset Size | Latest Score | Baseline/Current | Last Run |
|---|---:|---:|---|---|

## Known Gaps
- [ ]

## Next Exact Actions
1.
2.
3.

## Commands That Last Passed
```bash
# concise commands only
```
```

Keep this file factual and concise.

Never place credentials, API keys, document contents, or confidential information in this file.

Use it as the primary context-restoration mechanism between Claude Code sessions.

---

# 3. CURRENT VERIFIED PROJECT BASELINE

Treat the following as reported status, but verify it before relying on it.

## 3.1 Repository status

- Repository is pushed to GitHub.
- A clean commit exists.
- Local uncommitted changes may currently exist in:
  - `main.py`
  - `index.html`
  - `langgraph_agent.py`
- Those changes include a document-context bug fix.
- Check `git status` before making changes.
- Do not discard uncommitted work.
- Review and test it before committing.
- A background task may be converting “Ask a question” into a multi-turn chat interface with per-document conversational memory. Confirm its actual state before editing related files.

## 3.2 Existing core pipeline

The following pipeline is reported as working:

Upload  
→ PDF/DOCX extraction  
→ OCR/parsing  
→ clause extraction  
→ RAG retrieval using Chroma and Neo4j  
→ risk flagging  
→ summarization  
→ LangGraph human-in-the-loop pause/resume  
→ human approval

Preserve this pipeline and enhance it rather than rebuilding it.

## 3.3 Existing infrastructure

The reported Docker Compose stack includes:

- PostgreSQL
- Neo4j
- MinIO
- FastAPI application

All four containers have previously been verified healthy.

Chroma is used by the application. Determine whether it is embedded/persistent within the application container or a separate service. Do not create a duplicate Chroma deployment if the current arrangement is reliable.

Existing fixes include:

- CPU-only Torch handling instead of accidental CUDA dependencies.
- WSL2 resource-allocation fixes.

Do not reintroduce GPU or CUDA dependencies.

## 3.4 Existing data models

Reported existing models:

- `org_profile`
- `document`
- `clause`
- `risk_flag`
- `playbook_entry`
- `review_action`
- `audit_log`
- `summary_version`
- `knowledge_reference`

Inspect exact table and column names before creating migrations.

## 3.5 Existing seeded profiles

Reported seeded profiles:

- Vendor Procurement Unit
- Cross-Border Compliance Unit
- General Counsel Office

Each has clause checklists and risk thresholds.

Do not delete these profiles. Map or migrate them safely into the updated organization/standards model where appropriate.

## 3.6 Existing signature features

Preserve and test:

1. Organization-profile deviation scoring using cosine similarity.
2. Negotiation playbook generation with fallback positions and suggested redlines.
3. Cross-portfolio conflict detection for different terms across documents.
4. Evidence-weighted confidence combining:
   - OCR quality;
   - retrieval relevance;
   - LLM confidence.

## 3.7 Existing authentication

Reported current auth:

- Hardcoded reviewer roster.
- Roles:
  - admin;
  - reviewer;
  - senior_counsel.
- No public self-signup.
- Risk-based routing to senior counsel.

Enhance this incrementally. Do not introduce public registration.

## 3.8 Existing frontend

Reported existing surfaces:

1. Upload
2. Processing Status
3. Review Workspace
4. Portfolio & Risk Console
5. Approval & Escalation
6. Legal Operations Dashboard

Reported existing features:

- Light/dark theme.
- Persistent Document Detail view.
- Evaluation Matrix panel.
- Inline executive summary.
- Shared document context across tabs.
- Auto-fill rather than manual document ID/name entry.

Verify these features. Preserve working implementations.

---

# 4. PRODUCT VISION AND SCOPE

CounselGraph is a governed AI legal document intelligence system for Tata Group.

The first production scope is internal to Tata Group.

The architecture must also remain ready for future use as a multi-customer SaaS platform.

## 4.1 Current tenant interpretation

Current deployment:

- Customer: Tata Group
- Organizations/companies:
  - TCS
  - Tata Steel
  - Tata Motors
  - Tata Power
  - additional Tata companies
- Business units:
  - company-specific geographic or operational units
- Users:
  - Tata employees and authorized legal/compliance personnel

## 4.2 Future-ready interpretation

Future deployment may add external customers:

- Reliance
- Infosys
- Mahindra
- other enterprise groups

Each external customer must have completely isolated:

- users;
- documents;
- standards;
- clauses;
- risk rules;
- embeddings;
- audit records;
- approval policies;
- branding.

Build for Tata today, but retain a top-level `customer_id` boundary so the system can become SaaS-ready later.

---

# 5. TARGET END-TO-END WORKFLOW

The final document lifecycle must be:

1. Authenticated user signs in.
2. User sees a proper signed-in profile menu.
3. User uploads a PDF or DOCX.
4. User selects organization, business unit where applicable, jurisdiction, and document type.
5. System creates one persistent current-document context.
6. All subsequent screens derive the document name, ID, organization, status, and metadata from that context.
7. No tab asks the user to type the document name or document ID again.
8. The system processes the document:
   - secure upload;
   - OCR or direct text extraction;
   - parsing;
   - automatic confidentiality classification;
   - standards resolution;
   - clause extraction;
   - tenant-filtered RAG retrieval;
   - risk analysis;
   - summary generation.
9. First legal reviewer reviews:
   - clause extraction;
   - clause-level evidence;
   - risks;
   - missing clauses;
   - standards deviations;
   - confidence;
   - playbook suggestions;
   - summary.
10. Reviewer can:
   - accept;
   - edit;
   - reject;
   - comment;
   - escalate;
   - mark unusable.
11. Reviewer completes the first review.
12. System generates a consolidated Decision Brief.
13. Decision Brief is routed to the next approver.
14. Next approver sees:
   - executive summary;
   - contract metadata;
   - acceptable clauses;
   - flagged clauses;
   - missing clauses;
   - risk severity;
   - standards comparison;
   - key dates;
   - financial terms;
   - reviewer notes;
   - recommended action;
   - evidence links;
   - approval history.
15. Next approver can:
   - approve;
   - approve with changes;
   - reject;
   - send back.
16. Every step is written to the audit trail.
17. Evaluation results are available in a Stats/Evaluation tab using measured values only.

---

# 6. SINGLE-SITE DELIVERY REQUIREMENT

Do not build two separate user-facing sites.

The final product must appear as one integrated application.

Requirements:

- One public application origin.
- Frontend is the visible product.
- API endpoints remain internal application routes under `/api/...`.
- Infrastructure services are not publicly exposed in production.
- If the existing FastAPI application serves the frontend, preserve that pattern unless it is failing.
- If a reverse proxy is used, expose one public endpoint and route:
  - application pages;
  - `/api`;
  - static assets.
- Do not create a second public “API site”.

## 6.1 Swagger/OpenAPI behavior

The URL resembling:

`http://localhost:8000/docs#/default/logout_api_auth_logout_post`

is Swagger documentation for an API route, not a standalone product page.

Do not delete a useful logout API merely because it appears in Swagger.

Instead:

- Keep the backend logout route if it is used by the application.
- Use it from the signed-in user profile menu.
- Disable or protect FastAPI `/docs`, `/redoc`, and `/openapi.json` in production.
- Keep API documentation available in development only if useful.
- Do not expose Swagger as part of the final product navigation.

Example production configuration:

```python
app = FastAPI(
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
```

Use the project’s actual settings structure.

---

# 7. ENVIRONMENT, VIRTUAL ENVIRONMENT, AND DEPENDENCY HYGIENE

## 7.1 Local virtual environment

Create a repository-local virtual environment if one does not already exist:

Windows:

```powershell
cd D:\LegalAssistant
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Use `.venv`, not multiple venv directories.

Add these to `.gitignore` if missing:

```gitignore
.venv/
venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
htmlcov/
.env
.env.*
!.env.example
```

## 7.2 Do not unsafely modify global Python

The request is to remove dependencies installed outside a virtual environment, but do this safely:

- Do not bulk-uninstall system Python packages.
- Do not alter packages needed by Windows, WSL, Docker, or other projects.
- Audit which Python executable and pip executable are active.
- Record the result in `IMPLEMENTATION_STATUS.md`.
- Ensure all future project installations occur inside `.venv`.
- If project-specific packages are clearly known to have been globally installed, produce a removal command for user approval rather than silently uninstalling them.
- Remove obsolete repository-local virtual environments only after confirming `.venv` works.
- Do not commit the virtual environment.

Verify:

```powershell
where.exe python
where.exe pip
python -c "import sys; print(sys.executable)"
python -m pip --version
```

## 7.3 Docker dependency model

Docker deployment must not rely on the host virtual environment.

The Docker image must:

- use a CPU-compatible Python base;
- install pinned dependencies inside the image;
- avoid CUDA packages;
- run as a non-root user where feasible;
- include a health check;
- use environment variables;
- not copy `.env` or `.venv`;
- use persistent volumes for PostgreSQL, Neo4j, MinIO, and Chroma data where applicable.

---

# 8. SECRET AND CREDENTIAL POLICY

Gemini is already configured.

Do not search the repository repeatedly for credentials.

Do not print secrets.

Do not copy secrets into logs, status files, commits, tests, screenshots, or documentation.

Only inspect variable names and configuration wiring.

Use existing environment variables where available, including names equivalent to:

```env
GEMINI_API_KEY=
DATABASE_URL=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
NEO4J_URI=
NEO4J_USERNAME=
NEO4J_PASSWORD=
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
CHROMA_HOST=
CHROMA_PORT=
SECRET_KEY=
ENVIRONMENT=
DEFAULT_CUSTOMER_CODE=TATA_GROUP
```

Update `.env.example` with placeholders only.

If a real secret is found in Git history or tracked files:

1. do not display it;
2. report the file and variable name;
3. remove it from current code;
4. recommend credential rotation;
5. do not rewrite Git history without explicit approval.

---

# 9. TARGET DATA AND TENANCY MODEL

Inspect existing tables first. Implement this model through safe, idempotent migrations that preserve existing data.

Do not assume singular or plural table names. Adapt to actual project conventions.

## 9.1 Customer

Top-level enterprise customer.

Required fields:

- `id`
- `code`
- `name`
- `subscription_tier`
- `logo_url`
- `primary_color`
- `app_name`
- `is_active`
- timestamps

Seed one customer:

- code: `TATA_GROUP`
- name: `Tata Group`

## 9.2 Organization

Represents a Tata company.

Required fields:

- `id`
- `customer_id`
- `code`
- `name`
- `industry_sector`
- `parent_org_id`, nullable
- `default_risk_tolerance`
- `is_active`
- timestamps

Seed examples if safe:

- TCS
- Tata Steel
- Tata Motors
- Tata Power

Preserve the three existing `org_profile` rows.

Choose the lowest-risk migration strategy:

- enhance `org_profile`;
- map it to an `organization` table;
- or create an organization table with compatibility relationships.

Do not duplicate the same concept without a documented reason.

## 9.3 Business unit

Required fields:

- `id`
- `customer_id`
- `organization_id`
- `code`
- `name`
- `geography`
- `industry_vertical`, where useful
- `head_user_id`, nullable
- `is_active`

Examples:

- TCS North America
- TCS Europe
- TCS India

## 9.4 Jurisdiction

Required fields:

- `id`
- `code`
- `name`
- `legal_system`
- `privacy_regime`
- `data_localization_required`
- `compliance_notes`
- `is_active`

Seed:

- India — DPDP Act
- United States — applicable state privacy laws
- United Kingdom — UK GDPR
- European Union — GDPR
- Singapore — PDPA
- UAE — UAE PDPL
- Australia — Privacy Act

Do not present these entries as complete legal advice. They are metadata and demo references subject to legal validation.

## 9.5 Document type

Required fields:

- `id`
- optional `customer_id`
- `code`
- `name`
- `description`
- `default_confidentiality`
- optional approval template
- `is_active`

Examples:

- vendor agreement
- NDA
- service agreement
- employment agreement
- M&A agreement
- joint venture agreement
- internal policy
- SOP
- regulatory filing
- license agreement

## 9.6 Document enhancements

The existing document model must support:

- `customer_id`
- `organization_id`
- `business_unit_id`
- `jurisdiction_id`
- `document_type`
- `counterparty_name`
- `counterparty_type`
- `contract_value`
- `currency`
- `review_priority`
- `confidentiality_level`
- `classification_source`
- `classification_confidence`
- `classification_reasons`
- `sensitive_data_detected`
- `classification_timestamp`
- processing status
- original object-storage key
- timestamps

Backfill existing documents to Tata Group safely before adding non-null constraints.

## 9.7 Standards entities

Use existing `knowledge_reference`, `org_profile`, playbook, and risk configuration where possible.

The effective standards model must support:

### Standard clauses

- customer scope
- organization scope
- business-unit scope
- jurisdiction scope
- document-type scope
- clause type
- category:
  - preferred;
  - acceptable;
  - fallback;
  - prohibited.
- title
- clause text
- explanation
- usage guidance
- version
- approved by
- approval date
- effective date
- expiry date
- superseded clause reference
- tags
- active status

### Risk rules

- customer
- organization
- business unit
- jurisdiction
- document type
- clause type
- rule type
- structured condition
- severity
- rationale
- recommended action
- auto-escalation
- escalation role
- version and active status

### Compliance requirements

- optional customer
- jurisdiction
- industry sector
- requirement type
- clause type
- description
- legal reference
- severity
- linked standard clause
- active status

### Approval policies

- customer
- organization
- business unit
- document type
- confidentiality condition
- contract-value range
- risk severity trigger
- ordered approval sequence
- SLA
- active status

### Negotiation playbooks

Preserve the existing playbook implementation and extend its metadata to support:

- customer;
- organization;
- business unit;
- clause type;
- scenario;
- negotiation guidance;
- fallback positions;
- red lines;
- escalation trigger;
- approval metadata.

## 9.8 Confidentiality overrides

Track:

- document;
- original level;
- overridden level;
- reason;
- overridden by;
- timestamp.

## 9.9 Decision brief

Required logical fields:

- customer;
- document;
- generated by;
- generated at;
- executive summary;
- recommendation;
- acceptable clauses;
- flagged clauses;
- missing clauses;
- risk summary;
- standards comparison;
- key dates;
- financial terms;
- reviewer recommendation;
- approval status;
- approval metadata;
- version;
- previous version reference.

## 9.10 Approval chain

Required fields:

- customer;
- document;
- decision brief;
- sequence;
- approver role;
- approver user;
- status;
- decision;
- comments;
- required changes;
- trigger reason;
- decision timestamp.

## 9.11 Evaluation entities

Add only if not already represented:

### Evaluation dataset

- id
- name
- version
- task type
- description
- created by
- approval status
- timestamp

### Evaluation case

- dataset
- document reference or synthetic fixture
- task type
- input/query
- expected clause labels
- expected risks
- expected confidentiality
- expected answer
- expected contexts
- organization context
- jurisdiction context
- annotations
- split:
  - train;
  - validation;
  - test.

### Evaluation run

- dataset version
- application commit SHA
- model/provider configuration identifier
- prompt version
- retrieval configuration
- start/end timestamps
- status
- aggregate metrics
- notes

### Evaluation result

- run
- case
- task
- prediction
- ground truth
- metric values
- pass/fail
- error category
- evidence references

Do not store raw secrets or unnecessarily duplicate confidential document text.

---

# 10. TENANT AND ORGANIZATION ISOLATION

Implement two isolation boundaries:

1. Customer boundary:
   - Tata Group versus any future external customer.
2. Organization boundary:
   - TCS versus Tata Steel versus Tata Motors, except authorized group-level users.

Every relevant query must enforce customer scope.

Organization access must be enforced according to role.

Example behavior:

- normal organization user sees only their organization;
- business-unit user may be restricted to their business unit;
- group legal can see multiple Tata organizations;
- customer admin can manage the Tata customer;
- no user can cross into another customer.

Use database row-level security if Supabase/PostgreSQL auth integration is compatible with the current architecture.

If the application uses custom authentication rather than Supabase Auth, enforce tenant context in:

- database query layer;
- route dependencies;
- service calls;
- vector retrieval metadata;
- audit logs.

Do not write invalid RLS policies against non-existent `auth.users` columns.

If using RLS:

- create a proper `user_profile` table;
- derive claims safely;
- test policies directly in PostgreSQL.

Add negative tests proving:

- a TCS user cannot retrieve a Tata Steel document unless they have group-level permission;
- a Tata customer user cannot retrieve future customer data;
- vector retrieval cannot cross customer boundaries;
- document IDs cannot bypass authorization.

---

# 11. STANDARDS RESOLUTION STRATEGY

The same contract clause can have different legal significance for different Tata companies.

Implement a deterministic standards-resolution service.

## 11.1 Resolution hierarchy

Resolve approved clauses from most specific to most general:

1. Business Unit + Jurisdiction + Document Type
2. Business Unit + Document Type
3. Organization + Jurisdiction + Document Type
4. Organization + Document Type
5. Customer + Jurisdiction + Document Type
6. Customer + Document Type
7. Jurisdiction-only compliance requirement
8. Customer/group fallback

Return:

- selected standards;
- fallback standards;
- scope level;
- source;
- version;
- approval status;
- reason this standard applies.

## 11.2 Conflict and override rules

Do not silently combine conflicting rules.

Implement deterministic precedence:

- specific active rule overrides general active rule for the same rule key;
- mandatory jurisdiction requirements cannot be removed by a less-specific business preference;
- prohibited clauses remain prohibited unless an authorized explicit exception exists;
- expired/unapproved standards are excluded from high-confidence analysis;
- conflicts are shown to the reviewer and logged.

## 11.3 RAG filtering

The existing Chroma and Neo4j RAG must be enhanced, not replaced.

RAG retrieval must filter by:

- `customer_id`;
- applicable organization;
- business unit where present;
- jurisdiction;
- document type;
- clause type;
- active status;
- approved status;
- effective date.

Use a customer-specific collection or strict metadata filters.

Never retrieve another customer’s standards.

The retrieval result should include:

- source ID;
- source title;
- clause/policy text;
- scope level;
- organization;
- jurisdiction;
- version;
- approval status;
- relevance score.

## 11.4 Demonstration behavior

The same liability clause must be capable of producing different results:

- TCS IT-services context:
  - compared against TCS liability position.
- Tata Steel manufacturing context:
  - compared against manufacturing and environmental exposure rules.
- Tata Motors automotive context:
  - compared against product-liability standards.

The result must come from configured standards and risk rules, not hardcoded UI text.

---

# 12. AUTOMATIC CONFIDENTIALITY CLASSIFICATION

Implement automatic confidentiality classification immediately after usable text is available.

Human override must always remain available to authorized users.

## 12.1 Classification levels

- `highly_confidential`
- `confidential`
- `internal`
- `public`

## 12.2 Classification strategy

Use a hybrid approach:

### A. Deterministic signals

Evaluate:

- explicit labels:
  - strictly confidential;
  - confidential;
  - internal use only;
  - public filing.
- sensitive-content signals:
  - M&A;
  - valuation;
  - trade secrets;
  - board materials;
  - proprietary technology;
  - tender/bid information;
  - detailed pricing;
  - personal data;
  - salary information;
  - financial statements;
  - NDA language;
  - data-processing terms.
- document type default.
- metadata.

### B. Gemini structured classification

Use the existing Gemini client.

Provide:

- first relevant pages;
- document metadata;
- deterministic signals;
- allowed labels;
- exact JSON schema.

Expected output:

```json
{
  "level": "confidential",
  "confidence": 0.87,
  "reasons": [
    {
      "reason": "The document contains non-disclosure language.",
      "page": 1,
      "evidence": "Exact short evidence excerpt"
    }
  ],
  "sensitive_data_detected": [
    "personal_data",
    "financial_terms"
  ]
}
```

### C. Safe combination

Do not use a naive score that can downgrade explicit labels.

Rules:

- explicit “strictly confidential” cannot be downgraded by the LLM;
- if deterministic and LLM outputs disagree by more than one level, route for confirmation;
- low OCR confidence reduces classification confidence;
- ambiguous results default to the safer level;
- low-confidence results must be visibly marked;
- never silently mark a document public solely because no keywords were found.

## 12.3 Human override

Authorized users can override the result.

Require:

- new level;
- reason;
- user identity;
- timestamp.

UI must show:

- auto-detected level;
- confidence;
- evidence/reasons;
- classification source:
  - automatic;
  - manual override;
  - default;
- override history.

Every override must be audited.

## 12.4 Access-control integration

Classification influences access but must not rely only on UI hiding.

Example policy:

- highly confidential:
  - senior legal;
  - CLO;
  - explicitly assigned users.
- confidential:
  - authorized legal;
  - assigned business head.
- internal:
  - authorized internal users within scope.
- public:
  - authenticated/internal or configured public behavior.

Adapt to existing roles. Do not hardcode unauthorized broad access.

## 12.5 Confidentiality tests

Test at minimum:

- M&A document → highly confidential;
- NDA → confidential;
- internal SOP → internal;
- annual report/public filing → public;
- explicit label overrides LLM disagreement;
- low-OCR document does not become public;
- manual override writes audit record;
- unauthorized user cannot override;
- access control changes after override.

---

# 13. DOCUMENT PROCESSING PIPELINE

Preserve existing stages and add missing stages with idempotent job behavior.

Target sequence:

1. Upload
2. File validation
3. Secure object storage
4. Direct extraction or OCR
5. Page-level OCR quality
6. Automatic confidentiality classification
7. Document parsing
8. Standards resolution
9. Clause extraction
10. Tenant-filtered RAG retrieval
11. Risk analysis
12. Summary generation
13. Ready for human review
14. Human review
15. Decision brief generation
16. Approval routing
17. Final decision
18. Audit/export

## 13.1 Idempotency

Each stage must:

- know whether it already completed;
- avoid creating duplicate clauses or risk flags on retry;
- preserve useful partial results;
- record stage status and errors;
- support retry from the failed stage.

Use existing content-hash deduplication.

## 13.2 OCR and parsing

Preserve direct extraction for text PDFs and DOCX.

Use OCR only when needed.

Record:

- page number;
- text;
- OCR confidence;
- unreadable regions;
- table detection;
- page status.

Flag low-confidence pages for human review.

Do not send obviously unusable OCR text to the reasoning layer as if it were reliable.

## 13.3 Clause extraction

Extract and normalize at least:

- indemnity;
- limitation of liability;
- confidentiality;
- termination;
- renewal;
- payment;
- data protection;
- governing law;
- dispute resolution;
- audit rights;
- warranty;
- intellectual property;
- service levels;
- insurance;
- compliance obligations;
- environmental liability where applicable.

Each record should include:

- clause type;
- exact text;
- page/section reference;
- obligation owner;
- affected party;
- dates/deadlines;
- confidence;
- extraction method;
- verification status.

## 13.4 Risk analysis

Risk flags must distinguish:

- missing clause;
- non-standard language;
- ambiguous language;
- conflicting terms;
- duplicate clauses;
- prohibited language;
- compliance gap;
- value threshold;
- unusual governing law;
- excessive or unlimited liability;
- auto-renewal;
- asymmetric rights.

Each risk must include:

- severity;
- category;
- clause;
- document evidence;
- standards evidence;
- rationale;
- recommended action;
- confidence;
- applicable rule/source;
- reviewer decision and notes.

No material risk flag may be generated without document evidence or an explicit missing-clause rule.

## 13.5 Evidence-weighted confidence

Preserve and formalize the existing confidence composition.

Store components separately:

- OCR confidence;
- extraction confidence;
- retrieval relevance;
- standards applicability confidence;
- LLM confidence;
- final composed confidence.

Do not present the final confidence as a probability unless it is calibrated.

Label it as a system confidence score unless calibration has been performed.

---

# 14. SUMMARY GENERATION AND VERSION HISTORY

Preserve the existing inline executive summary.

Complete the missing editable-summary frontend.

Required summary sections:

- document overview;
- parties;
- scope;
- term;
- key obligations;
- financial terms;
- liability position;
- termination rights;
- confidentiality;
- data protection;
- governing law;
- key dates;
- major risks;
- missing clauses;
- recommended actions;
- evidence references.

## 14.1 Editing and versioning

Use the existing `summary_version` backend capability.

Add frontend support for:

- edit;
- save as new version;
- compare versions;
- restore a previous version where permitted;
- show who edited;
- show timestamp;
- show approval status;
- show whether content is AI-generated or human-edited.

Do not overwrite approved summary history.

---

# 15. HUMAN REVIEW AND DECISION BRIEF

## 15.1 First reviewer workspace

The reviewer must be able to:

- accept a clause;
- edit clause interpretation;
- reject an extraction;
- accept a risk;
- downgrade or upgrade severity with reason;
- add notes;
- escalate;
- mark the output unusable;
- complete review.

Each action must be audited.

## 15.2 Decision Brief generation

When the first reviewer selects “Complete Review & Generate Decision Brief”:

1. Validate review completeness.
2. Gather:
   - document metadata;
   - current approved summary version;
   - extracted clauses;
   - reviewed clauses;
   - risk flags;
   - reviewer decisions;
   - missing clauses;
   - standards comparison;
   - key dates;
   - obligations;
   - financial terms;
   - reviewer notes.
3. Generate a structured brief using Gemini.
4. Validate that every material statement references existing structured data or evidence.
5. Store a versioned brief.
6. Resolve the approval chain.
7. notify/route to the next approver.

## 15.3 Decision Brief contents

Required sections:

1. Executive summary
2. AI-generated recommendation
3. First reviewer’s recommendation
4. Acceptable terms
5. Items requiring attention
6. Missing clauses
7. Comparison to applicable company standards
8. Key dates
9. Key obligations
10. Financial terms
11. Evidence links
12. Review history
13. Approval chain

Allowed recommendations:

- approve;
- approve with changes;
- reject;
- escalate.

The UI must state that recommendations are decision support, not final legal advice.

## 15.4 Approval decisions

Next approver can:

- approve;
- approve with changes;
- reject;
- send back.

Rules:

- comments required for reject;
- required changes required for approve with changes;
- reason required for send back;
- only the current assigned approver or authorized override role can decide;
- decisions are immutable audit events;
- correction requires a new event/version, not deletion.

## 15.5 Approval chain strategy

Resolve from configured approval policies.

Fallback behavior if no specific policy exists:

1. legal reviewer;
2. senior counsel;
3. business head if high/critical risk or value threshold;
4. CLO if highly confidential or explicitly required.

Do not hardcode the contract-value threshold globally if an organization policy exists.

---

# 16. AUTHENTICATION AND USER PROFILE UX

Preserve the no-self-signup policy.

## 16.1 Remove public registration

- Remove register links.
- Remove register forms.
- Remove “registration open” status from the frontend.
- Disable or remove public registration routes if not needed.
- Do not remove admin-controlled user provisioning if useful.
- Add tests proving anonymous registration is unavailable.

## 16.2 Signed-in profile menu

Replace a standalone logout button with a proper signed-in profile control.

Show:

- user display name;
- role;
- organization;
- business unit if present;
- profile/avatar initials;
- dropdown menu;
- logout action.

Logout should:

- call the existing backend/session logout mechanism;
- clear local auth state;
- return to login;
- not require Swagger.

## 16.3 Role model

Preserve current roles and extend only as required:

- admin;
- reviewer;
- senior counsel;
- business head;
- CLO;
- group legal;
- legal operations/admin.

Use a permission mapping instead of scattering role-name checks throughout the code.

---

# 17. DOCUMENT-CENTRIC FRONTEND ARCHITECTURE

The uploaded/selected document must become the single source of truth across document-specific screens.

## 17.1 Shared current-document state

Use the existing document context if it works.

Otherwise implement a shared state mechanism appropriate to the current frontend.

The state must contain:

- document ID;
- filename;
- organization;
- business unit;
- jurisdiction;
- document type;
- confidentiality;
- processing status;
- counterparty;
- contract value;
- current review status.

Persist the selected document through:

- route parameter;
- URL;
- session/local state as appropriate.

A refresh should restore the same document context from the route.

Do not depend only on transient in-memory state.

## 17.2 Remove redundant inputs

No document-specific tab may ask for:

- document ID;
- document name;
- organization;
- document type;
- counterparty;

if those values already belong to the selected document.

Show them as read-only metadata.

Changing those values must be a deliberate document-metadata edit action, not a random input on every tab.

## 17.3 Global document header

Every document-specific screen should show a consistent header with:

- filename;
- organization/business unit;
- counterparty;
- document type;
- confidentiality badge;
- processing/review status;
- risk summary;
- upload date;
- user profile menu.

## 17.4 Rationalized product navigation

Do not create unnecessary top-level screens.

Keep or rationalize the current six surfaces:

1. Upload
2. Processing Status
3. Review Workspace
4. Portfolio & Risk Console
5. Approval & Escalation
6. Legal Operations Dashboard

Use contextual tabs/sub-tabs:

### Review Workspace

- Overview
- Clauses
- Risks
- Evidence/Standards
- Summary
- Ask CounselGraph

### Approval & Escalation

- Decision Brief
- Approval Chain
- Comments
- History

### Legal Operations Dashboard

- Operations
- Risk Trends
- Portfolio
- Evaluation/Stats

Decision Brief can be a sub-tab of Approval & Escalation rather than an unnecessary seventh top-level screen.

## 17.5 Chat behavior

Complete the in-progress document chat feature if not finished.

Requirements:

- multi-turn conversation;
- per-document session memory;
- prior messages remembered within the selected document session;
- switching documents must not leak chat context;
- citations to document pages/clauses;
- citations to standards references where used;
- insufficient-evidence response instead of hallucination;
- optional clear-chat action;
- chat history access controlled by document permissions.

Do not use uncontrolled long-term memory across tenants.

---

# 18. STANDARDS ADMINISTRATION

Implement a minimally viable standards administration area only if it does not already exist.

Restricted to authorized legal/admin roles.

Functions:

- view approved clauses;
- add draft standard;
- edit draft;
- submit for approval;
- approve/version;
- deactivate/expire;
- view applicability scope;
- manage risk rules;
- manage approval policies;
- manage compliance references;
- manage negotiation playbooks.

Do not allow normal reviewers to silently alter standards.

All standards changes must be audited and versioned.

---

# 19. API STRATEGY

Use the existing FastAPI routes and naming conventions.

Do not create duplicate endpoints merely to match this blueprint.

Ensure equivalent capabilities exist.

Required capability groups:

## Documents

- upload;
- retrieve;
- list with tenant filters;
- status;
- metadata update;
- secure download/view.

## Confidentiality

- run automatic classification;
- retrieve classification;
- override;
- retrieve override history.

## Clauses

- extract;
- list;
- retrieve evidence;
- reviewer update.

## Standards

- resolve applicable standards;
- retrieve clause standards;
- retrieve risk rules;
- retrieve compliance requirements;
- retrieve negotiation playbook.

## Risks

- analyze;
- list;
- reviewer decision;
- escalation.

## Summaries

- generate;
- retrieve latest;
- edit;
- create version;
- list versions;
- approve.

## Decision Brief

- generate;
- retrieve;
- list versions;
- submit decision;
- send back;
- retrieve approval chain;
- export.

## Chat

- ask;
- retrieve document chat history;
- clear session.

## Evaluation

- run a selected evaluation;
- list evaluation runs;
- retrieve metrics;
- retrieve per-task breakdown;
- compare runs.

## Audit

- retrieve document audit history;
- export permitted audit record.

Every endpoint must enforce:

- authentication;
- customer context;
- organization authorization;
- confidentiality access;
- request validation;
- structured errors.

---

# 20. AI EVALUATION STRATEGY

The product must demonstrate how intelligent and reliable the system is.

Do not use a single generic score as the only evaluation.

Use task-specific metrics.

RAGAS must be used where appropriate, but not misapplied.

## 20.1 Ground-truth principle

All reported evaluation metrics must be calculated against a labeled evaluation dataset.

Do not hardcode impressive percentages.

Do not display target values as achieved values.

The dashboard must clearly distinguish:

- target;
- baseline;
- current measured result;
- dataset size;
- dataset version;
- timestamp;
- application commit;
- model/prompt configuration.

If no evaluation has been run, show:

- “Not evaluated”;
- “No measured result”;
- or “Insufficient labeled cases”.

Never show fabricated numbers.

## 20.2 Dataset strategy

Use existing labeled evaluation entries and anomaly documents.

Current reported anomaly documents include:

- conflicting terms;
- duplicate clause;
- unusual governing law;
- ambiguous liability;
- scanned/no-text-layer;
- pricing table;
- other existing anomaly fixture.

Extend the dataset systematically.

Minimum MVP evaluation dataset:

- 20–30 documents if time constrained;
- include multiple document types;
- include multiple organizations;
- include text PDFs;
- include scanned PDFs;
- include tables/annexures;
- include negative cases where clauses are absent;
- include known risks and non-risks.

Preferred stronger dataset:

- 50+ documents;
- double-annotated subset;
- adjudicated disagreements;
- balanced clause categories;
- balanced confidentiality labels;
- organization-specific standards cases.

Ground-truth labels should include:

- clause spans;
- clause types;
- page references;
- obligation owner;
- expected missing clauses;
- risks;
- risk severity;
- confidentiality label;
- expected standards source;
- expected answers and relevant contexts for RAG Q&A.

## 20.3 Split and leakage controls

- Separate development and test sets.
- Do not embed ground-truth answers into production retrieval.
- Do not evaluate on documents used only to tune the prompt without labeling that run as validation.
- Record dataset version.
- Record model and prompt version.
- Keep the final test set stable.

---

# 21. CLAUSE EXTRACTION EVALUATION

Do not use RAGAS for clause extraction.

Use custom span/type extraction metrics.

## 21.1 Core metrics

Per clause type calculate:

- true positives;
- false positives;
- false negatives;
- precision;
- recall;
- F1;
- support.

Definitions:

```text
Precision = TP / (TP + FP)
Recall    = TP / (TP + FN)
F1        = 2 × Precision × Recall / (Precision + Recall)
```

Report:

- per-clause precision/recall/F1;
- micro average;
- macro average;
- weighted average;
- support.

## 21.2 Matching strategy

A predicted clause should match ground truth using a documented rule.

Support:

1. Exact span match.
2. Relaxed overlap match using token intersection-over-union.
3. Correct clause-type match.
4. Optional semantic match for differently bounded but substantively identical spans.

Report exact and relaxed metrics separately.

Do not count clauses as correct merely because the type exists somewhere in the document.

## 21.3 Additional clause metrics

Evaluate:

- page-reference accuracy;
- clause-boundary overlap;
- exact-text fidelity;
- obligation-owner accuracy;
- affected-party accuracy;
- date/deadline extraction accuracy;
- missing-clause detection precision/recall.

## 21.4 Clause performance visualization

Add a chart in:

`Legal Operations Dashboard → Evaluation/Stats → Clause Extraction`

Display measured data like:

```text
Clause Extraction Performance by Type
=============================================================
Clause Type         Precision     Recall        F1      Support
Liability              0.xx        0.xx       0.xx         n
Termination            0.xx        0.xx       0.xx         n
Confidentiality        0.xx        0.xx       0.xx         n
Indemnity              0.xx        0.xx       0.xx         n
Governing Law          0.xx        0.xx       0.xx         n
Data Protection        0.xx        0.xx       0.xx         n
-------------------------------------------------------------
Micro Average          0.xx        0.xx       0.xx         N
Macro Average          0.xx        0.xx       0.xx         N
```

Use horizontal grouped bars for precision and recall, plus an F1 value.

The chart may visually resemble:

```text
Liability       ███████████████░  Precision: measured | Recall: measured
Termination     ██████████████░░  Precision: measured | Recall: measured
Confidentiality ████████████████  Precision: measured | Recall: measured
Indemnity       █████████████░░░  Precision: measured | Recall: measured
Governing Law   ███████████████░  Precision: measured | Recall: measured
Data Protection ██████████████░░  Precision: measured | Recall: measured
```

Do not use the sample percentages as actual results.

Add:

- dataset selector;
- run selector;
- model/prompt version;
- sample count;
- “view errors” link.

Error analysis should show:

- missed clause;
- incorrect clause type;
- duplicate extraction;
- boundary mismatch;
- OCR-related failure.

---

# 22. RISK ANALYSIS EVALUATION

Do not use RAGAS as the main risk-classification metric.

Use classification and ranking metrics.

## 22.1 Risk-detection metrics

Calculate:

- precision;
- recall;
- F1;
- support;
- false-positive rate;
- false-negative rate.

Report by:

- severity;
- category;
- organization;
- document type;
- jurisdiction.

## 22.2 Critical-risk emphasis

Critical risk recall is especially important.

Calculate:

- critical-risk recall;
- high-risk precision;
- false-negative count for critical risks;
- top-k recall if risks are ranked;
- mean reciprocal rank or nDCG if reviewer prioritization is evaluated.

Do not combine these into an unexplained “intelligence score”.

If a composite score is shown, publish its formula and component values.

Suggested optional composite:

```text
Risk Decision-Support Score =
0.40 × Critical Risk Recall
+ 0.25 × High-Risk Precision
+ 0.35 × Overall Macro F1
```

Label this as an internal composite, not an industry-standard metric.

## 22.3 Severity classification

Use a confusion matrix for:

- critical;
- high;
- medium;
- low;
- no risk.

Visualize where the system confuses adjacent severities.

## 22.4 Reviewer agreement

Track operational metrics separately:

- reviewer acceptance rate;
- reviewer severity override rate;
- false-positive dismissal rate;
- escalation acceptance rate;
- “not usable” rate.

These are not substitutes for labeled accuracy metrics.

---

# 23. CONFIDENTIALITY EVALUATION

Do not use RAGAS.

Use multiclass classification metrics.

Calculate:

- overall accuracy;
- per-class precision;
- per-class recall;
- per-class F1;
- macro F1;
- weighted F1;
- confusion matrix;
- support.

Important safety metric:

- highly-confidential recall.

Also evaluate confidence calibration:

- reliability diagram;
- expected calibration error if dataset size is sufficient;
- Brier score where appropriate.

Do not claim that 90% confidence means 90% correctness unless calibration supports it.

Dashboard visualization:

- confusion matrix;
- per-class F1 bars;
- highly-confidential recall;
- override rate;
- override direction:
  - upgraded sensitivity;
  - downgraded sensitivity.

---

# 24. RAG AND CHAT EVALUATION USING RAGAS

RAGAS must be included for RAG-based components.

Use it for:

- document chat;
- clause-comparison Q&A;
- standards-based legal Q&A;
- compliance Q&A;
- generated answers based on retrieved evidence.

Do not use RAGAS as the primary evaluator for:

- clause extraction;
- risk severity classification;
- confidentiality classification;
- OCR accuracy.

## 24.1 RAGAS metrics

Use RAGAS metrics compatible with the installed version.

Target capabilities include:

- faithfulness;
- answer relevancy;
- context precision;
- context recall.

Where metric names differ by RAGAS version, use the current documented equivalent and pin the compatible version.

## 24.2 RAGAS evaluation record

Each case should include:

- question;
- generated answer;
- retrieved contexts;
- reference answer where available;
- reference contexts where available;
- document;
- organization;
- jurisdiction;
- expected standard source.

## 24.3 Legal-specific RAG tests

Include questions such as:

- What is the liability cap?
- What is the termination notice period?
- Does the agreement contain unilateral termination?
- What governing law applies?
- What personal-data obligations apply?
- How does this clause compare with the organization’s approved standard?
- Which high-risk terms require escalation?
- Which clauses are missing?
- What evidence supports this risk flag?

## 24.4 Citation evaluation

Add custom metrics beyond RAGAS:

- citation precision:
  - cited source actually supports the claim.
- citation recall:
  - material claims that require evidence have citations.
- citation correctness:
  - page/section exists.
- source-scope correctness:
  - retrieved standard belongs to the correct customer/organization context.

## 24.5 Insufficient-evidence behavior

Create negative cases where the answer is not in the document.

Measure:

- refusal/abstention accuracy;
- hallucination rate;
- unsupported-claim rate.

For legal use, faithfulness and unsupported-claim rate are more important than fluent answers.

---

# 25. RETRIEVAL AND STANDARDS EVALUATION

Evaluate retrieval independently from generation.

Metrics:

- Recall@K;
- Precision@K;
- Mean Reciprocal Rank;
- nDCG@K where relevance grades exist;
- wrong-tenant retrieval rate;
- wrong-organization retrieval rate;
- wrong-jurisdiction retrieval rate;
- expired/unapproved reference retrieval rate.

Required safety targets are not automatically achieved values:

- wrong-customer retrieval target: 0;
- unauthorized cross-organization retrieval target: 0;
- expired/unapproved high-confidence reference target: 0.

Add tests that fail if retrieval crosses customer boundaries.

---

# 26. OCR AND PARSING EVALUATION

Use metrics appropriate to OCR and document structure.

OCR:

- Character Error Rate;
- Word Error Rate;
- page success rate;
- unreadable-page detection recall;
- table-page detection accuracy.

Parsing:

- section-heading detection precision/recall;
- section-boundary overlap;
- table extraction success;
- annexure detection;
- signature-block detection.

Break down results by:

- native PDF;
- scanned PDF;
- poor scan;
- tables;
- long document;
- mixed format.

---

# 27. SUMMARY AND DECISION BRIEF EVALUATION

Use a combination of deterministic, human, and LLM-assisted evaluation.

Do not use RAGAS alone.

## 27.1 Deterministic checks

- all material risks are represented;
- all cited clause IDs exist;
- dates match structured extraction;
- financial terms match structured extraction;
- recommendation is one of the allowed labels;
- no unsupported organization standard is cited;
- no risk marked resolved unless reviewer action supports it.

## 27.2 Faithfulness

Use RAGAS faithfulness only where the summary/brief is treated as an answer grounded in supplied contexts.

Also calculate:

- claim-level support rate;
- citation precision;
- contradiction count;
- omission rate for critical risks.

## 27.3 Human review rubric

Have reviewers score:

- factual correctness;
- completeness;
- actionability;
- conciseness;
- risk prioritization;
- usefulness for decision;
- clarity;
- whether the original contract had to be reopened.

Track:

- reviewer acceptance rate;
- edit distance or percentage of brief changed;
- approval decision time;
- send-back rate;
- critical omission rate.

Do not claim “decision in under five minutes” until measured. Show it as a product goal until data exists.

---

# 28. EVALUATION/STATS DASHBOARD

Use or enhance the existing Evaluation Matrix panel in the Legal Operations Dashboard.

Create an `Evaluation/Stats` tab rather than a separate website.

## 28.1 Top summary cards

Display measured values only:

- Clause Extraction Macro F1
- Critical Risk Recall
- RAGAS Faithfulness
- Context Precision
- Confidentiality Macro F1
- Citation Precision
- Wrong-Tenant Retrieval Count
- Evaluation Dataset Size

Each card must show:

- measured value;
- target;
- previous run;
- trend;
- dataset version;
- run date;
- status:
  - pass;
  - warning;
  - fail;
  - not evaluated.

## 28.2 Charts

Include:

1. Clause precision and recall by type.
2. Clause F1 by type.
3. Risk confusion matrix.
4. Risk precision/recall by severity.
5. Confidentiality confusion matrix.
6. RAGAS metric radar or grouped bar chart.
7. Retrieval Recall@K and Precision@K.
8. OCR performance by file type.
9. Reviewer acceptance/override trend.
10. Evaluation run trend over time.
11. Error-category distribution.
12. Organization-specific standards accuracy.
13. Cross-tenant contamination safety metric.

## 28.3 Evaluation run details

Allow users to inspect:

- run ID;
- application commit SHA;
- Gemini model identifier;
- prompt version;
- embedding model;
- retrieval top-k;
- dataset version;
- number of cases;
- passed/failed cases;
- duration;
- estimated evaluation cost if available.

## 28.4 No fake benchmark claims

Do not display “industry standard” or “best in class” comparisons unless a credible cited benchmark exists and the task/dataset is comparable.

It is acceptable to show:

- internal target;
- previous CounselGraph run;
- current CounselGraph run.

---

# 29. OVERALL SYSTEM SCORE

An optional overall score may be displayed only if:

- all component metrics are measured;
- the formula is visible;
- missing metrics are not treated as zero or silently imputed;
- the dashboard also shows component scores.

Suggested internal composite:

```text
Overall CounselGraph Evaluation Score =
0.25 × Clause Extraction Macro F1
+ 0.25 × Risk Decision-Support Score
+ 0.20 × RAG Grounding Score
+ 0.10 × Confidentiality Macro F1
+ 0.10 × Citation Correctness
+ 0.10 × Workflow Human-Acceptance Score
```

Suggested RAG grounding score:

```text
RAG Grounding Score =
0.35 × Faithfulness
+ 0.25 × Answer Relevancy
+ 0.20 × Context Precision
+ 0.20 × Context Recall
```

Label these as internal composite scores.

Do not call them legal accuracy or probability of correctness.

---

# 30. EVALUATION DEPENDENCIES AND EXECUTION

Add only compatible packages.

Potential dependencies:

```txt
ragas
datasets
scikit-learn
pandas
numpy
```

Use the existing Gemini integration for RAGAS judge models if compatible.

Do not add OpenAI merely for evaluation if Gemini can be configured reliably.

Pin versions after proving compatibility.

Evaluation commands should be explicit, for example:

```bash
python -m evaluation.run --suite clause_extraction --dataset legal_eval_v1
python -m evaluation.run --suite risk --dataset legal_eval_v1
python -m evaluation.run --suite ragas --dataset legal_qa_v1
python -m evaluation.run --suite confidentiality --dataset confidentiality_v1
python -m evaluation.run --suite all --dataset legal_eval_v1
```

Adapt module paths to the repository.

The full evaluation suite should not run on every unit-test invocation because it may be slow and consume Gemini tokens.

Use test markers:

- unit;
- integration;
- docker;
- evaluation;
- expensive;
- end-to-end.

Mock Gemini in ordinary unit tests.

Run live Gemini evaluation only when explicitly requested or configured.

---

# 31. TESTING STRATEGY FOR EFFICIENCY

## 31.1 Baseline test before feature work

Before modifications:

1. activate `.venv`;
2. start Docker Compose;
3. run health checks;
4. run existing fast tests;
5. run one current happy-path document;
6. record results.

This creates a baseline.

## 31.2 Test pyramid

### Unit tests

Fast and isolated:

- confidentiality deterministic rules;
- score combination;
- standards precedence;
- approval-policy resolution;
- tenant filtering;
- decision-brief schema validation;
- evaluation metric formulas;
- route permission logic.

### Service integration tests

Use real PostgreSQL/Neo4j/Chroma/MinIO where appropriate:

- upload/storage;
- document persistence;
- vector metadata filtering;
- graph retrieval;
- pipeline resume;
- summary versioning;
- audit records.

### AI contract tests

Mock Gemini responses to test:

- structured JSON parsing;
- schema validation;
- retries;
- invalid output;
- timeout;
- evidence requirements.

### Live AI smoke tests

Small, explicit, and optional:

- one clause extraction;
- one confidentiality classification;
- one RAG answer;
- one decision brief.

Do not run live AI tests repeatedly during unrelated debugging.

### End-to-end tests

Run after major integration:

- upload to ready-for-review;
- reviewer completion to decision brief;
- approver decision;
- audit history;
- multi-tenant isolation.

## 31.3 Test commands

Determine existing tooling first.

Create concise commands or scripts such as:

```bash
# Fast local checks
pytest -m "unit and not expensive" -q

# Phase-specific tests
pytest tests/test_confidentiality.py -q
pytest tests/test_standards.py -q
pytest tests/test_decision_brief.py -q
pytest tests/test_evaluation_metrics.py -q

# Integration checkpoint
pytest -m integration -q

# Docker smoke test
pytest -m docker -q

# Full regression
pytest -q

# Optional live evaluation
pytest -m evaluation --run-live-evaluation
```

Do not invent paths if the project uses another structure. Adapt after inspection.

## 31.4 Debugging protocol

When a test fails:

1. Capture the first relevant traceback.
2. Classify failure:
   - environment;
   - migration;
   - fixture;
   - application logic;
   - AI schema;
   - external service;
   - obsolete test.
3. Reproduce with the smallest test.
4. Fix root cause.
5. Re-run the single failing test.
6. Run the related test file.
7. Run affected regression group.
8. Record the fix in the status file.

Do not repeatedly run the entire suite while debugging one unit failure.

---

# 32. DOCKER DEPLOYMENT TARGET

The final Docker Compose setup must provide one integrated application.

Infrastructure may include:

- application container;
- PostgreSQL;
- Neo4j;
- MinIO;
- Chroma if externalized;
- optional reverse proxy.

## 32.1 Production exposure

Public:

- one application port/domain.

Internal only:

- PostgreSQL;
- Neo4j Bolt/browser unless explicitly needed;
- MinIO admin console;
- Chroma;
- FastAPI docs.

Use Docker networks and avoid publishing internal ports in production Compose.

## 32.2 Health checks

Implement health checks for:

- application;
- PostgreSQL;
- Neo4j;
- MinIO;
- Chroma.

Application health must report dependencies without exposing credentials.

## 32.3 Startup order and migrations

- Infrastructure starts.
- Health checks pass.
- Migrations run once.
- Seed data is idempotent.
- Application starts.
- No migration is rerun destructively.
- No duplicate seeds are created.

## 32.4 Deployment documentation

Document:

- local `.venv` setup;
- local Docker setup;
- production Docker setup;
- environment variables;
- backups;
- migrations;
- health checks;
- how to create the first admin;
- how to seed Tata demo organizations;
- how to disable docs in production.

---

# 33. PHASED IMPLEMENTATION PLAN

Execute phases in this order.

Do not skip baseline verification.

## Phase 0 — Repository recovery and baseline

Tasks:

1. Inspect Git status.
2. Review uncommitted changes.
3. Confirm chat background task state.
4. Create `.venv`.
5. Verify active Python/pip.
6. Install dependencies into `.venv`.
7. Do not bulk-remove global packages.
8. Start Docker stack.
9. Verify service health.
10. Run existing tests.
11. Run one existing document through the pipeline.
12. Create `MASTER_BLUEPRINT.md`.
13. Create `IMPLEMENTATION_STATUS.md`.
14. Commit verified local bug fixes separately if appropriate.

Exit criteria:

- uncommitted changes understood;
- current working baseline recorded;
- existing pipeline smoke test result known;
- `.venv` working;
- Docker health known.

## Phase 1 — Architecture and migration audit

Tasks:

1. Inventory models/tables.
2. Inventory routes.
3. Inventory frontend surfaces.
4. Map existing features to blueprint.
5. Design idempotent migrations.
6. Decide how `org_profile` maps to customer/organization/BU.
7. Add customer boundary with Tata Group seed.
8. Backfill existing data safely.
9. Add indexes and constraints after backfill.
10. Add tenant-context tests.

Exit criteria:

- existing data preserved;
- Tata customer exists;
- organizations are scoped;
- documents have customer context;
- tests pass.

## Phase 2 — Auth, profile, and access control

Tasks:

1. Remove public registration UI/status.
2. Confirm registration endpoint behavior.
3. Add signed-in profile dropdown.
4. Wire logout through existing API.
5. Centralize permissions.
6. Enforce customer/org access.
7. Add negative authorization tests.
8. Configure FastAPI docs for development only.

Exit criteria:

- no public self-registration;
- proper profile menu;
- logout works;
- unauthorized cross-org access denied;
- Swagger not part of production UI.

## Phase 3 — Document-centric frontend

Tasks:

1. Verify existing shared document context.
2. Fix any document-context bugs.
3. Ensure route-based document restoration.
4. Remove all redundant document ID/name inputs.
5. Add universal document header.
6. Ensure every document tab uses current document.
7. Verify switching documents refreshes every component.
8. Prevent cross-document chat/session leakage.

Exit criteria:

- one selected document drives all screens;
- no redundant metadata inputs;
- refresh preserves document;
- all related components update together.

## Phase 4 — Automatic confidentiality

Tasks:

1. Add required fields/migration.
2. Build hybrid classifier.
3. Use existing OCR and Gemini.
4. Add structured evidence.
5. Add human override.
6. Add audit.
7. Integrate access control.
8. Add UI badge/details/override history.
9. Add tests.

Exit criteria:

- automatic classification runs;
- explicit labels are respected;
- low-confidence behavior is safe;
- human override works;
- access enforcement works;
- tests pass.

## Phase 5 — Standards hierarchy and tenant-filtered RAG

Tasks:

1. Extend existing knowledge references and profiles.
2. Add standards scope/version metadata.
3. Implement deterministic resolution.
4. Add risk/compliance/approval policies.
5. Enhance Chroma filtering.
6. Enhance Neo4j context where useful.
7. Preserve deviation scoring and playbooks.
8. Add seed/demo standards.
9. Add cross-tenant retrieval tests.

Exit criteria:

- TCS and Tata Steel can resolve different standards;
- wrong-customer retrieval is impossible in tests;
- result shows source/scope/version;
- existing RAG still works.

## Phase 6 — Risk and compliance enhancement

Tasks:

1. Apply resolved risk rules.
2. Add compliance-gap checks.
3. Preserve conflict/anomaly detection.
4. Include evidence and standards references.
5. Preserve evidence-weighted confidence.
6. Add reviewer actions and audit.
7. Add tests for:
   - unlimited liability;
   - missing termination;
   - conflicting clauses;
   - duplicate clauses;
   - unusual governing law;
   - ambiguous liability;
   - missing environmental clause;
   - missing data-protection clause.

Exit criteria:

- risk flags are evidence-grounded;
- org-specific behavior verified;
- false duplicate processing prevented;
- tests pass.

## Phase 7 — Summary editing and version history

Tasks:

1. Verify existing summary-version backend.
2. Add frontend editor.
3. Save new versions.
4. Show version history.
5. Add diff/compare where feasible.
6. Preserve approved versions.
7. Add tests.

Exit criteria:

- user can edit summary;
- every edit creates traceable version;
- old versions remain available.

## Phase 8 — Decision Brief and approval handoff

Tasks:

1. Add brief and approval-chain persistence.
2. Build brief generation service.
3. Validate evidence.
4. Resolve approval policy.
5. Add Decision Brief UI under Approval & Escalation.
6. Add approve/change/reject/send-back actions.
7. Add notifications if existing notification architecture supports them.
8. Add audit events.
9. Add PDF export if feasible after core workflow works.
10. Add tests.

Exit criteria:

- completed first review creates a brief;
- next approver can decide without re-entering document data;
- approval routing works;
- send-back creates a new cycle/version;
- audit complete.

## Phase 9 — Conversational document intelligence

Tasks:

1. Complete in-progress multi-turn chat.
2. Ensure per-document memory.
3. Add citations.
4. Add abstention behavior.
5. Add standards context when relevant.
6. Add authorization.
7. Add RAGAS-compatible logging/evaluation case format.
8. Add tests.

Exit criteria:

- chat remembers earlier turns for one document;
- switching documents does not leak context;
- answers contain evidence;
- insufficient evidence handled safely.

## Phase 10 — AI evaluation framework

Tasks:

1. Inventory existing labeled data.
2. Create versioned datasets.
3. Implement clause metrics.
4. Implement risk metrics.
5. Implement confidentiality metrics.
6. Implement OCR metrics where labels exist.
7. Implement retrieval metrics.
8. Integrate RAGAS for Q&A/RAG only.
9. Implement citation metrics.
10. Implement evaluation-run persistence.
11. Add CLI/API for running evaluations.
12. Add unit tests for metric formulas.
13. Run a small baseline evaluation.
14. Record measured results.

Exit criteria:

- metrics are reproducible;
- RAGAS used only for applicable tasks;
- no hardcoded metrics;
- baseline run stored;
- dataset size/version visible.

## Phase 11 — Evaluation/Stats dashboard

Tasks:

1. Enhance existing Evaluation Matrix panel.
2. Add summary cards.
3. Add clause precision/recall/F1 chart.
4. Add risk confusion matrix.
5. Add confidentiality confusion matrix.
6. Add RAGAS chart.
7. Add retrieval metrics.
8. Add run comparison.
9. Add error explorer.
10. Add dataset/run/model metadata.
11. Clearly distinguish targets from measured values.

Exit criteria:

- dashboard displays real run data;
- empty state is honest;
- no fabricated scores;
- chart breakdowns work.

## Phase 12 — Docker production readiness

Tasks:

1. Audit Compose and Dockerfile.
2. Preserve working CPU/Torch fix.
3. Ensure Docker dependencies are internal.
4. Add health checks.
5. Add migration startup strategy.
6. Use one public application entry point.
7. Disable Swagger in production.
8. Verify persistent volumes.
9. Verify clean build.
10. Run Docker end-to-end smoke test.

Exit criteria:

- clean Docker build;
- one public product site;
- all dependencies healthy;
- full demo path works;
- no reliance on host `.venv`.

## Phase 13 — Final regression and documentation

Tasks:

1. Run unit tests.
2. Run integration tests.
3. Run Docker smoke test.
4. Run one live Gemini smoke test if configured.
5. Run baseline AI evaluation.
6. Verify all existing features.
7. Update README.
8. Update architecture docs.
9. Update security docs.
10. Add evaluation guide.
11. Add demo script.
12. Update status file.
13. Prepare final commit.

Exit criteria:

- no unexplained failures;
- existing signature features preserved;
- new workflows pass;
- docs complete;
- repository ready to push.

---

# 34. REQUIRED TEST SCENARIOS

## 34.1 Existing functionality regression

Verify:

- upload;
- PDF extraction;
- DOCX extraction;
- scanned document handling;
- clause extraction;
- Chroma retrieval;
- Neo4j graph retrieval;
- risk flags;
- summary;
- LangGraph human pause/resume;
- playbook;
- conflict detection;
- evidence confidence;
- audit;
- theme toggle;
- current document context.

## 34.2 Tata standards comparison

Scenario A:

- organization: TCS
- jurisdiction: US
- document type: vendor agreement
- expected:
  - TCS standards used;
  - US privacy context considered;
  - no Tata Steel standards retrieved.

Scenario B:

- organization: Tata Steel
- jurisdiction: India
- document type: manufacturing/vendor agreement
- expected:
  - environmental obligations checked;
  - India data-protection context considered;
  - no TCS-only standard applied.

## 34.3 Confidentiality

- NDA;
- M&A;
- SOP;
- public filing;
- ambiguous scan;
- explicit confidentiality marking;
- override;
- authorization.

## 34.4 Decision handoff

- reviewer completes review;
- brief generated;
- next approver sees evidence;
- approve;
- approve with changes;
- reject;
- send back;
- version/audit behavior.

## 34.5 Evaluation

- metric formula unit tests;
- empty dataset;
- one-class dataset;
- no predictions;
- no ground-truth positives;
- duplicate predictions;
- exact and relaxed clause matching;
- RAGAS schema compatibility;
- evaluation run persistence;
- dashboard empty state;
- dashboard measured state.

---

# 35. UI ACCEPTANCE CRITERIA

## Upload

- File input.
- Organization.
- Business unit if available.
- Jurisdiction.
- Document type.
- Counterparty.
- Contract value/currency.
- Priority.
- No manual confidentiality requirement before upload unless used as optional user assertion.
- Automatic classification shown after extraction.
- Override available later.

## Processing

Show:

- upload;
- OCR;
- confidentiality;
- parsing;
- standards resolution;
- clause extraction;
- retrieval;
- risk analysis;
- summary;
- ready for review.

Each stage has:

- pending;
- active;
- complete;
- warning;
- failed.

## Review Workspace

The current document is already selected.

No document-name input.

Show:

- source document;
- clauses;
- confidence;
- risks;
- standards evidence;
- summary;
- reviewer actions;
- chat.

## Approval

Show Decision Brief and approval chain.

Do not ask for document ID/name.

Actions require correct comments/reasons.

## Dashboard Evaluation/Stats

Must include a clause-performance visualization equivalent to:

```text
Clause Extraction Performance by Type

Liability       Precision [bar]  Recall [bar]  F1 [value]
Termination     Precision [bar]  Recall [bar]  F1 [value]
Confidentiality Precision [bar]  Recall [bar]  F1 [value]
Indemnity       Precision [bar]  Recall [bar]  F1 [value]
Governing Law   Precision [bar]  Recall [bar]  F1 [value]
Data Protection Precision [bar]  Recall [bar]  F1 [value]

Overall:
Micro Precision | Micro Recall | Micro F1
Macro Precision | Macro Recall | Macro F1
Dataset size | Dataset version | Evaluation run
```

Use actual calculated values.

---

# 36. SECURITY REQUIREMENTS

- Validate upload MIME type and extension.
- Enforce file-size limit.
- Scan or safely handle malicious files where feasible.
- Use randomized object keys.
- Do not expose MinIO credentials.
- Do not log full legal text in normal application logs.
- Encrypt in transit.
- Use secure cookies/session behavior.
- Restrict CORS.
- Rate-limit login and expensive AI endpoints where feasible.
- Enforce authorization in backend services.
- Audit sensitive access.
- Disable production API docs.
- Never expose another tenant’s embeddings or graph nodes.
- Avoid sending more document content to Gemini than required.
- Document what content is sent to the model provider.
- Show AI-generated/pending-review status.

---

# 37. OBSERVABILITY AND COST CONTROL

Track:

- processing duration per stage;
- OCR failure;
- Gemini calls;
- Gemini retries/timeouts;
- token usage if returned;
- vector retrieval latency;
- graph retrieval latency;
- storage failures;
- reviewer turnaround;
- brief approval time;
- evaluation cost;
- pipeline failure rate.

Do not include raw contract text in telemetry.

Add:

- structured logs;
- correlation/document-job ID;
- retry counts;
- cost-aware caching;
- token budgets;
- chunk limits;
- model-routing configuration;
- evaluation cost estimate where available.

---

# 38. DOCUMENTATION DELIVERABLES

Update or create:

- `README.md`
- `MASTER_BLUEPRINT.md`
- `IMPLEMENTATION_STATUS.md`
- architecture documentation
- API documentation
- security notes
- multi-tenancy guide
- standards-management guide
- evaluation methodology
- evaluation dataset guide
- deployment guide
- demo script
- screenshots location
- contribution summary

README must include:

- product overview;
- current architecture;
- setup with `.venv`;
- Docker setup;
- environment variables;
- application URL;
- API docs development behavior;
- no-public-registration policy;
- processing workflow;
- confidentiality workflow;
- standards hierarchy;
- decision brief workflow;
- evaluation framework;
- how RAGAS is used;
- where RAGAS is not used;
- testing commands;
- deployed link placeholder;
- demo video placeholder.

---

# 39. AI EVALUATION MATRIX

Create a documented AI evaluation matrix separate from the project-delivery scoring rubric.

| Capability | Primary Metrics | Secondary Metrics | Evaluation Tool |
|---|---|---|---|
| OCR | CER, WER | page success, unreadable-page recall | Custom |
| Parsing | section precision/recall | boundary overlap, table success | Custom |
| Clause Extraction | precision, recall, F1 | span IoU, page accuracy, owner accuracy | Custom + sklearn |
| Missing-Clause Detection | precision, recall, F1 | false-positive rate | Custom |
| Risk Detection | precision, recall, F1 | critical recall, false-negative rate | Custom + sklearn |
| Risk Severity | macro F1 | confusion matrix, weighted F1 | sklearn |
| Confidentiality | macro F1, accuracy | highly-confidential recall, calibration | sklearn |
| Retrieval | Recall@K, Precision@K | MRR, nDCG, tenant leakage | Custom |
| Chat/RAG Answers | faithfulness, answer relevancy | context precision/recall | RAGAS |
| Citations | citation precision/recall | page/source correctness | Custom |
| Summary | claim support rate | critical omission, contradiction | Custom + human rubric |
| Decision Brief | factual correctness, completeness | actionability, decision time | Human + deterministic |
| Human Collaboration | acceptance rate | override/send-back rate | Operational analytics |
| Tenant Safety | wrong-tenant retrieval count | unauthorized access attempts | Security tests |

Implement this table in documentation and represent it in the Evaluation/Stats UI.

---

# 40. PROJECT DELIVERY EVALUATION MATRIX

Retain a separate product/project scoring rubric:

| Category | Weight |
|---|---:|
| Product understanding and legal framing | 10 |
| Product experience | 12 |
| Backend and API | 12 |
| AI workflow | 16 |
| Data, retrieval, and standards | 12 |
| Reliability, safety, and governance | 12 |
| Deployment readiness | 8 |
| Documentation | 8 |
| Demo quality | 5 |
| Team collaboration | 5 |
| Total | 100 |

Do not mix this project rubric with measured AI accuracy.

The dashboard may show both in separate sections:

- Product Delivery Readiness
- AI Quality Evaluation

---

# 41. FINAL DEFINITION OF DONE

The project is complete only when all applicable statements are true.

## Repository and environment

- `.venv` is documented and working.
- No dependency requires global Python.
- No secrets are committed.
- Git status is understood.
- All migrations are versioned.
- Docker clean build works.

## Product

- One integrated application site.
- No public signup.
- Signed-in profile dropdown works.
- Logout works through the application.
- Swagger disabled/protected in production.
- Selected document drives all document tabs.
- No repeated document-name/ID inputs.

## Pipeline

- Upload works.
- OCR/parsing works.
- Confidentiality auto-classification works.
- Human override works.
- Standards resolution works.
- Clause extraction works.
- Tenant-filtered RAG works.
- Risk analysis works.
- Summary works.
- Summary editing/versioning works.
- Decision Brief works.
- Approval chain works.
- Audit works.
- Chat works with per-document memory.

## Multi-tenancy

- Tata Group is current customer.
- Tata companies are organizations.
- Organization standards differ.
- Future customers can be added.
- Cross-customer data leakage tests pass.
- Unauthorized cross-organization tests pass.
- Vector retrieval isolation tests pass.

## Evaluation

- Versioned labeled dataset exists.
- Clause precision/recall/F1 works.
- Risk metrics work.
- Confidentiality metrics work.
- Retrieval metrics work.
- RAGAS is used for RAG/chat.
- RAGAS is not misused for extraction/classification.
- Citation metrics work.
- Evaluation runs are stored.
- Stats dashboard uses measured data.
- No fabricated scores.
- Clause performance chart is present.

## Governance

- Material claims have evidence.
- AI-generated content is labeled.
- Human approval remains authoritative.
- All reviewer and approver actions are audited.
- Sensitive data is not leaked into logs.
- Confidential access is enforced backend-side.

## Deployment

- Docker services healthy.
- One public application endpoint.
- Infrastructure services internal.
- Production docs disabled.
- Full upload-to-decision workflow passes in Docker.
- README and deployment guide complete.

---

# 42. FIRST ACTIONS TO EXECUTE NOW

Begin with Phase 0 only.

Execute these actions in order:

1. Open `D:\LegalAssistant`.
2. Run `git status`.
3. Inspect the uncommitted changes without discarding them.
4. Confirm whether the chat-conversion task completed and identify changed files.
5. Inspect the repository tree at a shallow depth.
6. Identify the existing Python environment.
7. Create `.venv` only if it does not exist.
8. Confirm `python` and `pip` point to `.venv`.
9. Install dependencies into `.venv`.
10. Inspect Docker Compose and running containers.
11. Start the stack if needed.
12. Run health checks.
13. Run the smallest existing test suite.
14. Run one current happy-path document smoke test if fixtures exist.
15. Create `MASTER_BLUEPRINT.md`.
16. Create `IMPLEMENTATION_STATUS.md`.
17. Record the verified baseline.
18. Stop and report:
    - existing working features;
    - uncommitted files;
    - baseline tests and results;
    - environment health;
    - exact Phase 1 plan.

Do not begin database migrations before completing and recording the Phase 0 baseline.

Do not print credentials.

Do not rebuild the application.

Do not create duplicate frontend or API sites.

Do not use fake evaluation results.
