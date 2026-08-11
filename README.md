# Legal GraphRAG

A small project for reviewing legal contracts with retrieval-augmented generation and a graph database, with a human always in the loop before anything gets finalized.

## What it does

You upload a PDF or DOCX contract. The pipeline runs OCR if needed, pulls out tables and clauses, checks for conflicting or missing clauses, flags risk with a confidence score and a recommended action, stores chunks in a Chroma vector index, and builds a graph of clauses/parties/references in Neo4j. Nothing is entered into the graph as final until a reviewer approves, rejects, or escalates it.

When you ask a question, a router decides whether to answer it with hybrid (vector + keyword) search or with a graph query, generates a draft answer, and stops for human approval before returning anything final. Reviewers can approve, ask for a revision (the model reasons over the same evidence again, no new retrieval), reject outright, or escalate to senior review.

Every stage of every job (ingestion or query) writes an append-only audit record, retrievable by job ID. An operations dashboard tab shows aggregate throughput, approval outcomes, and risk-category counts across everything processed so far.

## Why

Most RAG demos skip the part where someone actually checks the answer before it goes out. For legal documents that's the part that matters, so this project puts human review at every decision point:

1. Evidence checkpoint: a reviewer looks at what was retrieved before the model is allowed to generate anything.
2. Answer checkpoint: a reviewer approves, revises, rejects, or escalates the generated answer.
3. Ingestion checkpoint: a reviewer approves, rejects, or escalates the extracted clauses, conflicts, risk flags, and missing-clause gaps before any of it counts as reviewed.

## Project layout

```
src/legal_graphrag/
  agents/       router + specialist agents + the human-in-the-loop query pipeline
  api/          FastAPI app + a static HTML/CSS/JS frontend (api/static/index.html)
  graphrag/     Neo4j-backed extraction, risk flagging, and querying
  ingestion/    PDF/DOCX -> OCR -> chunking pipeline
  retrieval/    hybrid (vector + BM25) search
  llm_client.py, config.py, guardrails.py, resources.py
scripts/        demo script
tests/          unit tests, plus tests/eval/ for the labeled clause/risk eval
data/
  sample_contracts/     3 sample documents used for manual testing and the eval script
  clause_library/       approved reference clause language
  policy_references/    internal policy summaries
  risk_taxonomy.csv     risk categories, severities, and recommended actions
  uploads/, chroma_db/, metadata/   generated at runtime, gitignored
docs/           architecture, API reference, security notes, demo script
```

## Setup

1. Python 3.10+
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in a Gemini API key, your Neo4j URI/user/password, and change `ADMIN_USERNAME`/`ADMIN_PASSWORD` from the placeholder values (and set a real `SESSION_SECRET`, generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. Neo4j must be running and reachable for the graph features to work. A free instance works fine (see comments in `.env.example`).

## Running it

```
uvicorn legal_graphrag.api.main:app --reload --port 8000
```

Open `http://localhost:8000/ui` for the browser UI. You'll land on a login screen; sign in with `ADMIN_USERNAME`/`ADMIN_PASSWORD` from your `.env` before uploading or asking anything. There is a single hardcoded admin account, no signup, no user database.

You can also drive the pipeline directly in Python:

```python
from legal_graphrag.agents.legal_pipeline import build_legal_agent_graph
from langgraph.types import Command

app = build_legal_agent_graph()
config = {"configurable": {"thread_id": "job-1"}}

result = app.invoke({"question": "...", "collection_name": "..."}, config=config)
print(result["__interrupt__"])  # evidence checkpoint

result = app.invoke(Command(resume={"proceed": True, "reviewer": "jane"}), config=config)
print(result["__interrupt__"])  # answer checkpoint

result = app.invoke(Command(resume={"action": "approve", "reviewer": "jane"}), config=config)
print(result["final_answer"])
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md): how the ingestion and query pipelines work end to end.
- [`docs/api_documentation.md`](docs/api_documentation.md): every endpoint, request/response shapes, and error cases.
- [`docs/security_notes.md`](docs/security_notes.md): auth, secrets, prompt injection defense, and what's out of scope.
- [`docs/demo_script.md`](docs/demo_script.md): a walkthrough covering a vendor agreement review, a missing-clause gap, and the escalation flow.

## Tests

```
pytest
```

This runs the unit tests plus `tests/eval/test_eval_thresholds.py`, which scores the real clause-extraction and risk-flagging pipeline against `tests/eval/labeled_eval_set.json` (hand-labeled expected clauses/risks for the sample contracts). That eval test needs a live `GEMINI_API_KEY` since it makes real LLM calls; it's skipped automatically if one isn't set. Run `python tests/eval/run_eval.py` directly for the full breakdown (clause recall, risk precision, missing-clause accuracy per document).

## Notes

- All model outputs are treated as drafts until a human approves them; nothing is written back to the graph unless it was explicitly approved.
- Clause extraction and risk assessment are LLM-based and not perfectly deterministic between runs; the eval script and demo script call this out rather than hiding it.
- Secrets/API keys are read from environment variables only, never committed. See `docs/security_notes.md` for details.
