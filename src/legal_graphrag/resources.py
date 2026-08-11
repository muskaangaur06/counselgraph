"""Shared external-resource singletons (Neo4j, Chroma, embedder, reranker, checkpointer).

Kept out of LangGraph state since state has to be checkpoint-serializable and
these aren't; graph nodes just pull them from here instead.
"""

from __future__ import annotations

import os
from typing import Optional

from sentence_transformers import SentenceTransformer, CrossEncoder
from langgraph.checkpoint.memory import MemorySaver
import chromadb

from . import config
from .graphrag.neo4j_store import Neo4jGraphStore
from .ingestion.pdf_pipeline import EMBEDDING_MODEL_NAME as VECTOR_EMBEDDING_MODEL, CHROMA_PERSIST_DIR

RERANKER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_store: Optional[Neo4jGraphStore] = None
_embedder: Optional[SentenceTransformer] = None
_reranker: Optional[CrossEncoder] = None
_chroma_client = None
_checkpointer = None


def get_store() -> Neo4jGraphStore:
    global _store
    if _store is None:
        candidate = Neo4jGraphStore(
            uri=config.require_env("NEO4J_URI"),
            user=config.require_env("NEO4J_USER"),
            password=config.require_env("NEO4J_PASSWORD"),
        )
        candidate.ensure_schema()  # if this raises, _store stays None so a retry starts fresh
        _store = candidate
    return _store


def close_store() -> None:
    """Closes and clears the cached Neo4j driver singleton so a later get_store()
    call reconnects instead of reusing a closed driver (e.g. across app lifespans
    in the same process, as happens with multiple TestClient instances in tests)."""
    global _store
    if _store is not None:
        _store.close()
        _store = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(VECTOR_EMBEDDING_MODEL)
    return _embedder


def get_reranker() -> CrossEncoder:
    """Cross-encoder used by hybrid_search.rerank(). Lazy-loaded; see preload_models() to load eagerly instead."""
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANKER_MODEL_NAME)
    return _reranker


def get_chroma_collection(collection_name: str):
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    return _chroma_client.get_or_create_collection(name=collection_name)


def get_checkpointer():
    """Shared checkpointer for the ingestion/query graphs. Backend via CHECKPOINTER_BACKEND:
    "memory" (default, single-process, lost on restart), "postgres", or "redis"."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    backend = os.getenv("CHECKPOINTER_BACKEND", "memory").lower()

    if backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver
        conn_string = config.require_env("CHECKPOINTER_POSTGRES_URL")
        saver_cm = PostgresSaver.from_conn_string(conn_string)
        saver = saver_cm.__enter__()  # kept open for the life of the process
        saver.setup()
        _checkpointer = saver
    elif backend == "redis":
        from langgraph.checkpoint.redis import RedisSaver
        conn_string = config.require_env("CHECKPOINTER_REDIS_URL")
        saver = RedisSaver.from_conn_string(conn_string)
        saver.setup()
        _checkpointer = saver
    else:
        _checkpointer = MemorySaver()

    return _checkpointer


def preload_models(include_neo4j: bool = True) -> None:
    """Eagerly load the embedder/reranker (and optionally Neo4j) at startup instead of on the first request."""
    get_embedder()
    get_reranker()
    get_checkpointer()
    if include_neo4j:
        get_store()
