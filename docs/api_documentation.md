# API Documentation

Base URL when running locally: `http://localhost:8000`. Interactive Swagger docs are auto-generated at `/docs`.

## Authentication

Login is a single hardcoded admin account (no user database, no signup), set via `ADMIN_USERNAME`/`ADMIN_PASSWORD` in the environment. Logging in issues a signed, httponly session cookie; every route below (except `/api/auth/login`, `/health`, and `/ui`) requires that cookie. Missing or expired session returns `401`. See `docs/security_notes.md` for details.

### `POST /api/auth/login`

**Request body**: `{ "username": "admin", "password": "..." }`

**Response** (`200`): `{"status": "ok"}`, with a `Set-Cookie: lg_session=...` header. Session is valid for 12 hours. Rate-limited separately and more tightly than other endpoints (10 attempts/minute per source IP) to slow down brute-forcing.

**Errors**: `401` for wrong credentials.

### `POST /api/auth/logout`

Clears the session cookie. Always returns `200`.

### `GET /api/auth/me`

Requires a valid session. Returns `{"status": "ok"}` if the session is valid; used by the frontend to check login state on page load.

## Rate limiting

Write endpoints (`POST`) are rate-limited per client (by session cookie, or by IP if not logged in) using a sliding window: `RATE_LIMIT_MAX_REQUESTS` requests per `RATE_LIMIT_WINDOW_SECONDS` (defaults: 30 requests / 60 seconds). Exceeding the limit returns `429`.

## Ingestion

### `POST /api/ingestion/jobs`

Uploads a document and starts the ingestion pipeline. Blocks until the pipeline pauses at the approval checkpoint (or completes, if something failed early).

**Request** (`multipart/form-data`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | `.pdf` or `.docx`, max size set by `MAX_PDF_SIZE_MB` (default 50 MB) |
| `vendor_name` | string | no | Linked to the contract as a `Party` with role "Vendor" |
| `contract_name` | string | no | Defaults to the filename if omitted |
| `collection_name` | string | no | Chroma collection name; derived from the filename if omitted |

**Response** (`200`): a `JobResponse`.

```json
{
  "thread_id": "c242976a-...",
  "state": "paused",
  "checkpoint": "ingestion_approval_request",
  "payload": {
    "job_id": "bf8ecf7d-...",
    "document_context": { "contract_type": "Service", "parties": [...], "subject_matter": "..." },
    "num_clauses": 8,
    "num_high_risk_flags": 1,
    "num_conflicts": 0,
    "num_missing_clauses": 1,
    "high_risk_flags": [ { "clause_id": "...", "risk_level": "high", "reason": "...", "confidence": 0.9, "recommended_action": "Escalate to senior counsel", "text": "..." } ],
    "conflicts": [],
    "missing_clauses": [ { "clause_type": "termination", "reason": "..." } ]
  }
}
```

**Errors**: `400` for an unsupported file type, oversized file, or a page count over `MAX_PAGES_PER_INGESTION`.

### `POST /api/ingestion/jobs/{thread_id}/resume`

Resumes a paused ingestion job with a reviewer decision.

**Request body**:

```json
{ "decision": { "action": "approve", "reviewer": "your name", "comments": "optional" } }
```

`action` is one of `"approve"`, `"reject"`, or `"escalate"`. `"escalate"` routes the contract to senior review instead of finalizing an approval/rejection (its `Contract.approved` stays `null` in the graph). Older callers can send `{"approved": true|false, ...}` instead of `action`, which maps to approve/reject.

**Response**: another `JobResponse`, either `state: "completed"` (with `result.status` one of `"approved"`, `"rejected"`, `"escalated"`) or, in principle, `state: "paused"` again if a future version adds more checkpoints to this graph.

### `GET /api/ingestion/jobs/{thread_id}`

Reads the current state of an ingestion job without side effects. Returns `404` if the thread doesn't exist.

## Query

### `POST /api/query/jobs`

Starts the query pipeline: router -> retrieval -> auditor -> synthesizer, pausing at the evidence checkpoint.

**Request body**:

```json
{ "question": "What is the limitation of liability clause?", "collection_name": "sample_vendor_agreement_pdf", "metadata_filter": null }
```

**Response**: a `JobResponse` paused at `checkpoint: "evidence_approval_request"`, with the retrieved evidence and the auditor's sufficiency verdict in `payload`.

### `POST /api/query/jobs/{thread_id}/resume`

Resumes either checkpoint in the query graph. The client just keeps calling this with the next decision until `state == "completed"`.

At the evidence checkpoint:

```json
{ "decision": { "proceed": true, "reviewer": "your name", "comments": "optional" } }
```

Set `"escalate": true` instead of (or alongside) `proceed: false` to route to senior review rather than a plain rejection.

At the answer checkpoint:

```json
{ "decision": { "action": "approve", "reviewer": "your name", "edited_answer": "optional override", "comments": "optional" } }
```

`action` is one of `"approve"`, `"revise"`, `"reject"`, or `"escalate"`. `"revise"` requires non-empty `comments`, since that feedback is what the LLM reasons over to produce the next draft; it loops back to the same checkpoint (capped at `max_answer_revisions`, default 3, after which it's force-rejected).

### `GET /api/query/jobs/{thread_id}`

Reads the current state of a query job. Returns `404` if the thread doesn't exist.

## Audit

### `GET /api/audit/{job_id}`

Returns the full append-only audit trail and reviewer decisions for a job (works for either a `job_id` from an ingestion job or a `query_job_id` from a query job, since both are stored as `job_id` internally). Returns `404` if nothing is found.

```json
{
  "job_id": "bf8ecf7d-...",
  "audit_trail": [
    { "audit_id": "...", "actor": "system", "action": "job_started", "details": "document=sample.pdf", "timestamp": "2026-08-10T07:53:23Z" },
    { "audit_id": "...", "actor": "test_reviewer", "action": "review_decision", "details": "action=approve", "timestamp": "..." }
  ],
  "reviewer_decisions": [
    { "decision_id": "...", "approved": true, "reviewer": "test_reviewer", "comments": "looks fine", "decided_at": "..." }
  ]
}
```

## Operations Dashboard

### `GET /api/dashboard/stats`

Returns aggregate throughput and risk metrics across every document and question ever processed.

```json
{
  "document_jobs_by_status": { "processing": 2, "approved": 5, "rejected": 1 },
  "query_jobs_by_status": { "answered": 3 },
  "risk_flags_by_level": { "high": 4, "medium": 9, "low": 6 },
  "missing_clause_flags": 2,
  "conflicting_clause_pairs": 1,
  "total_contracts": 8
}
```

## Health

### `GET /health`

Unauthenticated. Returns `{"status": "ok"}`. Used by the frontend's status indicator and suitable for a container/uptime health check.

## Frontend

### `GET /ui`

Serves the bundled static HTML/CSS/JS frontend. No session required for this route itself; the page shows a login screen and calls `/api/auth/login` before making any other API calls.
