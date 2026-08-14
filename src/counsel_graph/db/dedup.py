"""Content-based dedup for clauses (Postgres) and chunks (Chroma).

Both use the same idea: derive a deterministic id/hash from normalized content
instead of a fresh random UUID on every ingestion run, so re-running ingestion
for the same document upserts existing rows/vectors instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import re


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def clause_content_hash(clause_type: str | None, party: str | None, extracted_text: str) -> str:
    """hash of normalized(clause_type + party + extracted_text). Used as the dedup
    key for the clause table: re-ingesting the same document upserts by this hash
    instead of always inserting a new row."""
    normalized = _normalize(f"{clause_type or ''}|{party or ''}|{extracted_text}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_deterministic_id(document_id: str, chunk_index: int, text: str) -> str:
    """Deterministic Chroma id derived from document_id + chunk_index + content hash,
    so re-running ingestion for the same document upserts the same vector rather
    than inserting a duplicate under a new random UUID."""
    content_hash = hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:16]
    return f"{document_id}:{chunk_index}:{content_hash}"
