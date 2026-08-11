# Architecture

## Overview

The system is built around two LangGraph state machines, both backed by the same Neo4j graph store and Chroma vector store, and both stopping at human-in-the-loop checkpoints before anything is finalized.

1. **Ingestion graph**: PDF/DOCX upload -> OCR (if needed) -> chunking -> clause extraction -> conflict detection -> risk flagging -> missing-clause detection -> human approval.
2. **Query graph**: question -> router -> hybrid search or graph query -> evidence checkpoint -> answer synthesis -> answer checkpoint (with a revise loop).

## Ingestion pipeline

```
Upload (PDF/DOCX)
   |
   v
Extract text + tables (pdfplumber for PDF, python-docx for DOCX)
   |
   v
Detect low-text pages -> OCR (Tesseract, PDF only)
   |
   v
Clean + chunk text (sliding window, page/section tracked per chunk)
   |
   v
Extract contract metadata (parties, contract type, dates) -- one LLM call
   |
   v
LangGraph: start_job -> extract_clauses -> link_same_clause -> detect_conflicts
           -> risk_flag -> missing_clause -> human_approval -> apply_decision
```

Each clause gets an LLM-assigned `clause_type`, a `confidence` score, and an embedding (via sentence-transformers) so it can be vector-matched against clauses in other contracts already in the graph. Risk flags carry a `risk_level`, `reason`, `confidence`, and `recommended_action`. Missing clauses are found by comparing the extracted clause types against a per-contract-type checklist (`graphrag/extraction.py::_EXPECTED_CLAUSES_BY_CONTRACT_TYPE`), not by another LLM call, since "is X absent" is a set-difference problem the code can just check.

The `human_approval` node pauses the graph with `interrupt()` and hands back a JSON payload summarizing what needs review. Resuming with `{"action": "approve"|"reject"|"escalate", ...}` continues the graph and records the decision, an audit entry, and (if approved or rejected) the contract's approval status in Neo4j.

## Query pipeline

```
Question
   |
   v
Router (LLM classifies: hybrid | graph | direct) -- chooses a dense/sparse
   |                                                blend weight per query style
   +--------------------+
   |                     |
   v                     v
Hybrid search        Graph query
(Chroma + BM25,       (template match for known question shapes,
 rerank)               else LLM-generated read-only Cypher)
   |                     |
   +--------------------+
   |
   v
Auditor (LLM checks: is this evidence sufficient?)
   |
   v
Human evidence checkpoint (approve / reject / escalate)
   |
   v
Synthesizer (drafts an answer, cites evidence, flags risk level + uncertainty)
   |
   v
Human answer checkpoint (approve / revise / reject / escalate)
   |            |
   |            +--> revise_answer (LLM re-reasons over the same evidence
   |                  with the reviewer's feedback, loops back to the
   |                  checkpoint, capped by max_answer_revisions)
   v
Finalize -> (if graph route + approved) record as an AnsweredQuestion node
```

The graph-query path first checks a small set of hand-written Cypher templates for common question shapes (e.g. "vendor X, same clause elsewhere, interpreted by multiple judgments") before falling back to LLM-generated Cypher. Generated Cypher is checked against a regex denylist (`is_read_only_cypher`) that rejects `CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`, and APOC/`LOAD CSV` calls, so a bad generation can't mutate the graph.

## Storage

- **Chroma**: document chunk embeddings (text + tables), used for hybrid search. One collection per ingested document.
- **Neo4j**: the structured graph. `DocumentJob`/`QueryJob` -> `Contract` -> `Clause` -> `RiskFlag`, plus `Party`, `Judgment`, `ReviewerDecision`, and an append-only `AuditRecord` trail attached to every job.
- **LangGraph checkpointer**: in-memory by default (single-process only), swappable to Postgres or Redis via `CHECKPOINTER_BACKEND` for anything beyond local development.

## Data assets (`data/`)

- `sample_contracts/`: three generated sample documents (a vendor MSA, a mutual NDA, and an internal vendor-onboarding policy) used for manual testing and the eval script.
- `clause_library/approved_clauses.json`: reference clause language the retrieval layer can be pointed at for RAG-grounded comparisons.
- `policy_references/internal_policies.json`: internal policy summaries in the same spirit, for policy-document review journeys.
- `risk_taxonomy.csv`: the risk categories, severities, and recommended actions the risk-flagging prompt is meant to align with.

These are not currently wired into the retrieval layer automatically (the hybrid search only searches the ingested document's own chunks); they exist as the "approved knowledge base" referenced conceptually by the ingestion/reasoning prompts and as fixtures for the eval script.

## Why LangGraph specifically

The two things that mattered here were: (1) a graph that can pause mid-execution and resume later from exactly where it stopped, potentially days later, and (2) typed state that's easy to reason about across a dozen-plus nodes. LangGraph's `interrupt()`/`Command(resume=...)` plus its checkpointer abstraction cover both directly, without hand-rolling a state machine and a polling API.
