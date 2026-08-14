"""Postgres-backed operational data store: org profiles, documents, clauses, risk
flags, playbooks, review actions, audit log, summary history, and the knowledge
library used by RAG retrieval. Neo4j remains the graph store for clause/relationship
traversal (SAME_CLAUSE_AS, CONFLICTS_WITH, INTERPRETED_BY); Postgres is now the
canonical store for everything else that used to live in flat JSON/CSV files.
"""

from .session import get_engine, get_session, init_db  # noqa: F401
from . import models  # noqa: F401
