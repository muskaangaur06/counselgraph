"""Hybrid (dense + lexical) retrieval with metadata filtering and cross-encoder
reranking, used by HybridSearchAgent. Dense embeddings handle semantic matches,
BM25 covers exact strings (clause numbers, citations) that embeddings miss;
both get fused and reranked with a cross-encoder on the top candidates.

Known limitation: the BM25 index is built in-memory from the whole Chroma
collection, which doesn't scale great; a vector DB with native hybrid search
would be a better long-term fix.
"""

from __future__ import annotations

import re
from typing import Optional

from rank_bm25 import BM25Okapi

from ..resources import get_chroma_collection, get_embedder, get_reranker

# in-memory BM25 index, cached per collection
_bm25_cache: dict[str, tuple[BM25Okapi, list[str], list[str], list[dict]]] = {}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _get_bm25_index(collection_name: str, refresh: bool = False):
    if not refresh and collection_name in _bm25_cache:
        return _bm25_cache[collection_name]

    collection = get_chroma_collection(collection_name)
    raw = collection.get(include=["documents", "metadatas"])
    ids, texts, metadatas = raw["ids"], raw["documents"], raw["metadatas"]
    bm25 = BM25Okapi([_tokenize(t) for t in texts])

    _bm25_cache[collection_name] = (bm25, ids, texts, metadatas)
    return _bm25_cache[collection_name]


def invalidate_bm25_cache(collection_name: str) -> None:
    """Call after ingesting new documents into `collection_name` so the next search rebuilds the BM25 index."""
    _bm25_cache.pop(collection_name, None)


def get_full_document_text(collection_name: str) -> str:
    """Pulls every chunk for a collection and reconstructs the document in order,
    for whole-document summarization (as opposed to similarity-search retrieval,
    which has nothing to rank a generic "summarize this" question against)."""
    collection = get_chroma_collection(collection_name)
    raw = collection.get(include=["documents", "metadatas"])
    texts, metadatas = raw["documents"], raw["metadatas"]

    ordered = sorted(
        zip(texts, metadatas),
        key=lambda pair: pair[1].get("chunk_index", 0),
    )
    return "\n\n".join(text for text, _ in ordered)


# builds a Chroma `where` clause from the metadata filter, plus a matching
# Python-side predicate for the BM25 branch (which `where` can't reach)

def build_where_clause(filters: dict) -> Optional[dict]:
    clauses: list[dict] = []
    if filters.get("contract_type"):
        clauses.append({"contract_type": filters["contract_type"]})
    if filters.get("governing_law_country"):
        clauses.append({"governing_law_country": filters["governing_law_country"].upper()})
    if filters.get("min_effective_date_epoch") is not None:
        clauses.append({"effective_date_epoch": {"$gte": filters["min_effective_date_epoch"]}})
    if filters.get("max_effective_date_epoch") is not None:
        clauses.append({"effective_date_epoch": {"$lte": filters["max_effective_date_epoch"]}})
    if filters.get("min_monetary_value") is not None:
        clauses.append({"monetary_value": {"$gte": filters["min_monetary_value"]}})
    if filters.get("max_monetary_value") is not None:
        clauses.append({"monetary_value": {"$lte": filters["max_monetary_value"]}})

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _matches_where(meta: dict, where: Optional[dict]) -> bool:
    """Python-side equivalent of build_where_clause(), for filtering BM25 candidates."""
    if not where:
        return True
    if "$and" in where:
        return all(_matches_where(meta, clause) for clause in where["$and"])
    (field, condition), = where.items()
    value = meta.get(field)
    if isinstance(condition, dict):
        if "$gte" in condition and not (value is not None and value >= condition["$gte"]):
            return False
        if "$lte" in condition and not (value is not None and value <= condition["$lte"]):
            return False
        return True
    return value == condition


def _dense_search(collection_name: str, query: str, top_k: int, where: Optional[dict]) -> list[dict]:
    """Chroma applies `where` natively DURING the ANN search: filtering here was never the issue."""
    collection = get_chroma_collection(collection_name)
    embedder = get_embedder()
    query_embedding = embedder.encode([query], normalize_embeddings=True).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=top_k, where=where)

    hits = []
    if results["ids"]:
        for doc_id, doc, meta, dist in zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            hits.append({"id": doc_id, "text": doc, "metadata": meta, "dense_distance": dist})
    return hits


def _sparse_search(collection_name: str, query: str, top_k: int, where: Optional[dict]) -> list[dict]:
    """Filter to eligible docs first, then rank by BM25: filtering after ranking could drop valid matches under a restrictive filter."""
    bm25, ids, texts, metadatas = _get_bm25_index(collection_name)
    scores = bm25.get_scores(_tokenize(query))

    eligible = [i for i in range(len(ids)) if _matches_where(metadatas[i], where)]
    eligible_ranked = sorted(eligible, key=lambda i: scores[i], reverse=True)[:top_k]

    return [
        {"id": ids[i], "text": texts[i], "metadata": metadatas[i], "bm25_score": float(scores[i])}
        for i in eligible_ranked
    ]


def reciprocal_rank_fusion(id_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Rank-only fusion: score(d) = sum of 1/(k + rank) across lists. Alternative to convex_combination_fusion."""
    scores: dict[str, float] = {}
    for id_list in id_lists:
        for rank, doc_id in enumerate(id_list):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def min_max_normalize(scores: dict[str, float], reverse: bool = False) -> dict[str, float]:
    """Min-max normalize scores to [0, 1]. Set reverse=True if lower is better (e.g. distance)."""
    if not scores:
        return {}
    vals = list(scores.values())
    min_val, max_val = min(vals), max(vals)
    if min_val == max_val:
        return {doc_id: 1.0 for doc_id in scores}
    normalized = {}
    for doc_id, score in scores.items():
        if reverse:
            norm_score = (max_val - score) / (max_val - min_val)
        else:
            norm_score = (score - min_val) / (max_val - min_val)
        normalized[doc_id] = float(norm_score)
    return normalized


def convex_combination_fusion(
    dense_hits: list[dict],
    sparse_hits: list[dict],
    alpha: float = 0.4,
    dense_score_key: str = "dense_distance",
    sparse_score_key: str = "bm25_score",
    dense_is_distance: bool = True,
) -> dict[str, float]:
    """Weighted convex combination: score = alpha * dense_norm + (1 - alpha) * sparse_norm.

    Default alpha=0.4 weights BM25 more heavily (40% dense / 60% sparse) since
    legal text leans on exact wording and citations. Raise alpha if queries
    skew more paraphrase/semantic.
    """
    raw_dense = {h["id"]: h[dense_score_key] for h in dense_hits}
    raw_sparse = {h["id"]: h[sparse_score_key] for h in sparse_hits}

    norm_dense = min_max_normalize(raw_dense, reverse=dense_is_distance)
    norm_sparse = min_max_normalize(raw_sparse, reverse=False)

    all_doc_ids = set(norm_dense.keys()).union(set(norm_sparse.keys()))
    fused_scores = {}
    for doc_id in all_doc_ids:
        d_score = norm_dense.get(doc_id, 0.0)
        s_score = norm_sparse.get(doc_id, 0.0)
        fused_scores[doc_id] = (alpha * d_score) + ((1.0 - alpha) * s_score)

    return dict(sorted(fused_scores.items(), key=lambda item: item[1], reverse=True))


def rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """Cross-encoder rerank of the fused candidate set, using the shared reranker singleton from resources."""
    if not candidates:
        return []
    reranker = get_reranker()
    pairs = [(query, c["text"]) for c in candidates]
    scores = reranker.predict(pairs)
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)[:top_k]


def hybrid_search(
    collection_name: str,
    query: str,
    metadata_filter: Optional[dict] = None,
    dense_k: int = 20,
    sparse_k: int = 20,
    fusion_k: int = 15,
    final_k: int = 6,
    fusion_method: str = "convex",
    alpha: float = 0.4,
) -> list[dict]:
    """Dense search + BM25 search (both metadata-filtered) -> fusion ("convex" or
    "rrf") -> parties_contains post-filter -> cross-encoder rerank -> top final_k."""
    metadata_filter = metadata_filter or {}
    where = build_where_clause(metadata_filter)

    dense_hits = _dense_search(collection_name, query, dense_k, where)
    sparse_hits = _sparse_search(collection_name, query, sparse_k, where)

    by_id: dict[str, dict] = {}
    for h in dense_hits + sparse_hits:
        by_id.setdefault(h["id"], {}).update(h)

    if fusion_method == "rrf":
        fused_scores = reciprocal_rank_fusion(
            [[h["id"] for h in dense_hits], [h["id"] for h in sparse_hits]]
        )
    else:
        fused_scores = convex_combination_fusion(
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            alpha=alpha,
            dense_is_distance=True,  # Chroma returns distances where lower = better
        )

    fused_ranked_ids = list(fused_scores.keys())[:fusion_k]
    candidates = [by_id[i] for i in fused_ranked_ids if i in by_id]

    parties_needle = metadata_filter.get("parties_contains")
    if parties_needle:
        needle = parties_needle.lower()
        candidates = [c for c in candidates if needle in (c["metadata"].get("parties", "") or "").lower()]

    return rerank(query, candidates, top_k=final_k)
