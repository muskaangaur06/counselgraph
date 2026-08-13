"""Section 25: retrieval evaluation, independent of generation. Builds a small
throwaway Chroma collection from synthetic chunks tagged with two different
tenants (org_profile_id), scores Recall@K/Precision@K/MRR against a hand-labeled
query->expected-chunk set, and separately measures wrong-tenant retrieval rate
by querying tenant A's collection with a metadata filter and confirming zero
tenant-B chunks ever come back. Uses the real embedder -- if it fails to load
(documented host risk: Windows "paging file too small"), the whole domain is
reported as skipped rather than crashing the eval run or fabricating scores.
"""

from __future__ import annotations

import os
import tempfile
import uuid

_LABELED_QUERIES = [
    {"query": "What is the termination notice period?", "expected_chunk_id": "chunk-termination",
     "tenant": "tenant-a"},
    {"query": "What is the liability cap?", "expected_chunk_id": "chunk-liability",
     "tenant": "tenant-a"},
    {"query": "What governing law applies to this agreement?", "expected_chunk_id": "chunk-governing-law",
     "tenant": "tenant-b"},
]

_TENANT_CHUNKS = {
    "tenant-a": [
        ("chunk-termination", "Either party may terminate this Agreement upon 60 days prior written notice."),
        ("chunk-liability", "Each party's aggregate liability shall not exceed the fees paid in the prior 12 months."),
        ("chunk-confidentiality-a", "The parties shall keep all confidential information secret for 5 years."),
    ],
    "tenant-b": [
        ("chunk-governing-law", "This Agreement shall be governed by the laws of the State of Delaware."),
        ("chunk-payment-b", "Payment is due within 30 days of invoice."),
        ("chunk-warranty-b", "Vendor warrants the goods are free from material defects for 90 days."),
    ],
}


def run_retrieval_eval(top_k: int = 3) -> dict:
    try:
        from sentence_transformers import SentenceTransformer
        import chromadb
    except Exception as e:  # noqa: BLE001
        return {"status": "skipped", "reason": f"embedder/chromadb unavailable: {type(e).__name__}: {e}"}

    persist_dir = os.path.join(tempfile.gettempdir(), f"retrieval_eval_chroma_{uuid.uuid4().hex[:8]}")
    try:
        embedder = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as e:  # noqa: BLE001
        return {"status": "skipped", "reason": f"embedder failed to load: {type(e).__name__}: {e}"}

    client = chromadb.PersistentClient(path=persist_dir)
    collection_name = f"retrieval_eval_{uuid.uuid4().hex[:8]}"
    collection = client.get_or_create_collection(name=collection_name)

    ids, texts, metadatas = [], [], []
    for tenant, chunks in _TENANT_CHUNKS.items():
        for chunk_id, text in chunks:
            ids.append(chunk_id)
            texts.append(text)
            metadatas.append({"tenant": tenant})

    embeddings = embedder.encode(texts, normalize_embeddings=True).tolist()
    collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

    case_results = []
    recall_hits, precision_scores, reciprocal_ranks = [], [], []
    wrong_tenant_hits = 0
    total_filtered_queries = 0

    for case in _LABELED_QUERIES:
        query_embedding = embedder.encode([case["query"]], normalize_embeddings=True).tolist()

        # unfiltered: scores Recall@K/Precision@K/MRR across the whole collection
        unfiltered = collection.query(query_embeddings=query_embedding, n_results=top_k)
        retrieved_ids = unfiltered["ids"][0]
        hit = case["expected_chunk_id"] in retrieved_ids
        recall_hits.append(1.0 if hit else 0.0)
        precision_scores.append(
            len([i for i in retrieved_ids if i == case["expected_chunk_id"]]) / len(retrieved_ids)
            if retrieved_ids else 0.0
        )
        reciprocal_ranks.append(1.0 / (retrieved_ids.index(case["expected_chunk_id"]) + 1) if hit else 0.0)

        # tenant-filtered: THIS is the actual safety check -- querying with a
        # tenant filter must never surface another tenant's chunk id
        total_filtered_queries += 1
        filtered = collection.query(
            query_embeddings=query_embedding, n_results=top_k,
            where={"tenant": case["tenant"]},
        )
        other_tenant_ids = {cid for t, chunks in _TENANT_CHUNKS.items() if t != case["tenant"] for cid, _ in chunks}
        leaked = [cid for cid in filtered["ids"][0] if cid in other_tenant_ids]
        wrong_tenant_hits += len(leaked)

        case_results.append({
            "domain": "retrieval", "case_label": case["query"], "passed": hit and not leaked,
            "scores": {"hit_at_k": 1.0 if hit else 0.0, "reciprocal_rank": reciprocal_ranks[-1]},
            "detail": {"retrieved": retrieved_ids, "expected": case["expected_chunk_id"], "leaked_cross_tenant_ids": leaked},
        })

    try:
        import shutil
        shutil.rmtree(persist_dir, ignore_errors=True)
    except OSError:
        pass

    return {
        "status": "completed",
        "recall_at_k": round(sum(recall_hits) / len(recall_hits), 3),
        "precision_at_k": round(sum(precision_scores) / len(precision_scores), 3),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 3),
        "wrong_tenant_retrieval_count": wrong_tenant_hits,
        "wrong_tenant_retrieval_rate": round(wrong_tenant_hits / total_filtered_queries, 3) if total_filtered_queries else 0.0,
        "case_results": case_results,
        "case_count": len(_LABELED_QUERIES),
        "top_k": top_k,
    }
