# Security Notes

## Secrets

All credentials (`GEMINI_API_KEY`, `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`, `ADMIN_PASSWORD`, `SESSION_SECRET`) are read from environment variables via `.env`, never hardcoded in source and never committed (`.env` is in `.gitignore`). `.env.example` documents every variable with a placeholder value. If you ever paste a real key or password into a chat, a shared doc, or a screenshot, treat it as compromised and rotate it, since there is no way to "unsee" that once it's been shared.

## Authentication

Login is a single hardcoded admin account: `ADMIN_USERNAME`/`ADMIN_PASSWORD` in the environment, no user database, no signup, no way to create additional accounts. `POST /api/auth/login` checks the submitted credentials against those two environment variables and, on success, issues a signed, httponly, `SameSite=Lax` session cookie (`lg_session`) valid for 12 hours. Every route except `/api/auth/login`, `/health`, and `/ui` requires a valid session cookie; a missing or expired one returns `401`.

The cookie is signed (not encrypted) with `itsdangerous`, keyed by `SESSION_SECRET` (falls back to `API_KEY`, then an insecure hardcoded default if neither is set, the server prints a warning on startup in that case). Signing means a client can see the cookie's decoded content is just `{"user": "admin"}` plus a signature, but cannot forge a valid one without knowing `SESSION_SECRET`. Login attempts are rate-limited more tightly than other endpoints (10/minute per source IP) to slow down credential-guessing.

For anything beyond local development on `localhost`, change `ADMIN_PASSWORD` from the default and set a real `SESSION_SECRET` before exposing the port.

## Rate limiting

A sliding-window rate limiter caps write requests per session (or per source IP if not logged in). This is in-memory and per-process, so it resets on restart and doesn't share state across multiple worker processes; a real multi-worker deployment would need a shared store (Redis) for this to be meaningful.

## Prompt injection

Any text pulled from an uploaded document (clause text, user questions passed into Cypher generation) is wrapped with `wrap_untrusted()` before being handed to the LLM: the wrapper tags it as `<data_label>...</data_label>` and explicitly instructs the model to treat it as data, not instructions, and to ignore anything inside it that looks like a command. This is a mitigation, not a guarantee. It reduces the odds a maliciously crafted contract could hijack a prompt, but a sufficiently adversarial input could still fail.

## Generated Cypher

When the LLM generates a Cypher query for the graph route, it's checked against `is_read_only_cypher()` before ever reaching Neo4j: a regex denylist rejects any query containing `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, `CALL apoc.*`, or `LOAD CSV`, case-insensitively. A query that fails this check is never executed; the request fails with an error instead. This is defense against the LLM generating a mutating query, not a general SQL/Cypher-injection defense, since the model is the one producing the query text in the first place, not an external attacker's raw input.

## File upload validation

Uploads are checked before they reach a parser or an LLM call: file extension must be `.pdf` or `.docx`, content-type must roughly match, the file can't be empty, and it can't exceed `MAX_PDF_SIZE_MB` (default 50 MB). Page count is capped separately at `MAX_PAGES_PER_INGESTION` (default 300) after parsing, since clause extraction runs once per chunk and an unbounded document means an unbounded LLM bill.

## Human-in-the-loop as a safety control

Every LLM-generated output, a clause extraction, a risk flag, a drafted answer, sits behind a human approval step before it's treated as final. Nothing is written back into the graph as an approved fact until a named reviewer has approved it, and every decision (approve, reject, revise, escalate) is recorded with the reviewer's name, a timestamp, and their comments in an append-only `AuditRecord`. This is the core governance mechanism the whole system is built around, not an afterthought bolted onto an autonomous pipeline.

## What is not covered

This is a local/demo-scale project, and a few things a production deployment would need are intentionally out of scope here:

- No encryption at rest for uploaded files (`data/uploads/`) or the Chroma vector store; they're plain files on disk.
- No role-based access control, and only a single hardcoded admin account. Anyone with the admin password has full access to every action; there's no way to distinguish "this reviewer can approve high-risk contracts" from "this reviewer can only view." The free-text "reviewer name" field on each decision is for record-keeping, not real per-user identity or authorization.
- No structured, redacted logging. `print()` statements in the pipeline nodes can include clause text and document names in server logs; if this were deployed anywhere with shared log access, that would need to change.
- No automated secret scanning or dependency vulnerability scanning in the repo.

These are reasonable gaps for a first-release/demo scope, but should be treated as a follow-up list before any real legal documents with actual confidential content go through a deployed instance of this.
