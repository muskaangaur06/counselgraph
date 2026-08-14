# Environment Setup

## Primary path: `docker compose up`

This brings up the full local stack: Postgres (operational data store), Neo4j
(clause/graph relationships), MinIO (uploaded-document object storage), and the
FastAPI app itself. Chroma runs embedded inside the API container (see note
below), not as a separate service.

### 1. Configure environment variables

```
cp .env.example .env
```

Fill in at minimum:

- `GEMINI_API_KEY` (get a free key at https://aistudio.google.com/apikey)
- `REVIEWER_1_PASSWORD` / `REVIEWER_2_PASSWORD` / `REVIEWER_3_PASSWORD` (or set
  `REVIEWERS` as a single JSON array instead, see the comment in `.env.example`)
- `SESSION_SECRET` (generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `POSTGRES_PASSWORD`, `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` (pick your own values;
  `docker-compose.yml` uses these same variables to configure Postgres and MinIO themselves)

Leave `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` as-is if you want compose's own
Neo4j container (the compose file overrides `NEO4J_URI` to point at the `neo4j`
service internally regardless of what's in `.env`, so only `NEO4J_PASSWORD` needs
to match between `.env` and what you want the container's auth to be).

### 2. Bring up the stack

```
docker compose up --build
```

This builds the API image (multi-stage `Dockerfile`: a builder stage installs
Python deps including `psycopg2`/`boto3`/`sqlalchemy`, poppler-utils and
tesseract-ocr are installed in the runtime stage for OCR), then starts Postgres,
Neo4j, MinIO, and the API, waiting on each dependency's healthcheck.

### 3. Seed org profiles and the knowledge library

Once Postgres is up (first `docker compose up` run only, or after a `docker
compose down -v` reset):

```
docker compose exec api python scripts/seed_db.py
```

This creates the "Vendor Procurement Unit", "Cross-Border Compliance Unit", and
"General Counsel Office" org profiles and migrates `data/clause_library/approved_clauses.json`
and `data/risk_taxonomy.csv` into seed `knowledge_reference` rows.

### 4. Open the app

- UI: http://localhost:8000/ui
- API docs: http://localhost:8000/docs
- Neo4j browser: http://localhost:7474
- MinIO console: http://localhost:9001

### Chroma note

The app uses `chromadb.PersistentClient` (embedded, on-disk mode), not the
Chroma server's HTTP API. There is deliberately no separate `chroma` container
in `docker-compose.yml`; instead, a named volume (`chroma_data`) is mounted
directly into the `api` container at its on-disk Chroma path, so vector data
persists across container recreation the same way Postgres/Neo4j/MinIO data
does. Migrating to the Chroma server (`chromadb.HttpClient`) would be a
separate follow-up if multi-process/horizontal scaling of the API becomes a
requirement.

### Resetting local state

```
docker compose down -v   # drops all named volumes: Postgres, Neo4j, Chroma, MinIO data
```

---

## Secondary option: running natively (no Docker)

Useful for local development without container overhead, or on a machine where
Docker isn't available. Falls back gracefully: if `DATABASE_URL` is unset, the
app uses a local SQLite file at `data/counsel_graph.db` instead of Postgres; if
`STORAGE_ENDPOINT`/`STORAGE_ACCESS_KEY`/`STORAGE_SECRET_KEY`/`STORAGE_BUCKET`
are unset, uploads fall back to local disk at `data/uploads/`.

1. `pip install -r requirements.txt` (system packages: `poppler-utils`,
   `tesseract-ocr`, needed for OCR, same as the Docker runtime stage)
2. `cp .env.example .env` and fill in `GEMINI_API_KEY` + Neo4j credentials
   (a free instance at https://console.neo4j.io works fine for this)
3. `python scripts/seed_db.py` (creates the SQLite file and seeds org profiles,
   or points at Postgres if `DATABASE_URL` is set)
4. `uvicorn counsel_graph.api.main:app --reload --port 8000`
5. Open http://127.0.0.1:8000/ui

### Cloud deployment (Neo4j Aura, managed Postgres, etc.)

The same native-run steps apply; just point `NEO4J_URI`/`DATABASE_URL`/
`STORAGE_ENDPOINT` at your managed instances instead of localhost. Because
`CHECKPOINTER_BACKEND` defaults to an in-memory LangGraph checkpointer, a
multi-worker/multi-instance cloud deployment should set
`CHECKPOINTER_BACKEND=postgres` (reusing the same Postgres instance as
`DATABASE_URL`, or a separate one) so paused approval steps survive a worker
restart; see the comment block in `.env.example` and `resources.get_checkpointer()`.
