"""Section 4: Org-Profile Deviation Scoring.

For each extracted clause, compares its embedding against the org profile's
approved clause language for that clause_type using the same embedding model
already used for Chroma (no extra LLM call). Produces a structured diff:
approved position summary, this contract's position, numeric delta, and a
confidence derived from the embedding-similarity magnitude.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = (np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _summarize(text: str, max_chars: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "..."


def compute_deviation(clause_text: str, clause_embedding: list[float], clause_type: Optional[str],
                       org_profile_id: Optional[str], embedder, standards_context: Optional[dict] = None) -> Optional[dict]:
    """Returns None if there's no approved language on file for this clause_type
    (nothing to compare against), else a dict with:
      approved_position, contract_position, deviation_score (cosine similarity,
      1.0 = identical), delta (1 - similarity), confidence (derived from the
      similarity magnitude, not another LLM call).

    standards_context (optional, Phase 5): business_unit_id/jurisdiction_id/
    document_type/customer_id -- when given, the approved language is resolved
    through the 8-level standards hierarchy instead of org_profile_id alone.
    """
    if not clause_type:
        return None
    try:
        from ..db.repository import get_approved_clause_language
        approved_text = get_approved_clause_language(clause_type, org_profile_id, standards_context=standards_context)
    except Exception:
        approved_text = None

    if not approved_text:
        return None

    approved_embedding = embedder.encode(approved_text, normalize_embeddings=True).tolist()
    similarity = cosine_similarity(clause_embedding, approved_embedding)
    delta = 1.0 - similarity
    # confidence: a similarity near 0 or 1 is a clear-cut signal (high confidence
    # in the deviation reading itself); mid-range similarity is the ambiguous
    # zone where "how different is this really" is least certain.
    confidence = round(1.0 - (4.0 * similarity * (1.0 - similarity)), 3)

    return {
        "clause_type": clause_type,
        "approved_position": _summarize(approved_text),
        "contract_position": _summarize(clause_text),
        "deviation_score": round(similarity, 4),
        "delta": round(delta, 4),
        "confidence": confidence,
    }
