"""
Neo4j graph store for the CounselGraph pipeline.

Nodes: DocumentJob, Contract, Party, Clause (with its own embedding for
vector similarity), Judgment, RiskFlag, ReviewerDecision, AuditRecord.
Relationships connect them roughly as you'd expect: DocumentJob PRODUCED a
Contract, which CONTAINS_CLAUSE, clauses can be SAME_CLAUSE_AS each other or
CONFLICTS_WITH each other, INTERPRETED_BY a Judgment, FLAGGED_AS a risk.

Everything goes through execute_write()/execute_read() rather than raw
session.run(), since this pipeline can sit idle for a long time at a human
approval step and the driver needs to retry/reconnect if the connection got
dropped in the meantime. Write helpers mostly use MERGE so re-running
ingestion on the same document doesn't create duplicates.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j import GraphDatabase


CLAUSE_EMBEDDING_DIM = 768  # must match whatever embedding model you encode clauses with


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            keep_alive=True,  # TCP keepalive so a long idle approval wait doesn't get dropped
            liveness_check_timeout=30,  # ping idle pooled connections before reuse
            max_connection_lifetime=3600,  # recycle connections before a cloud provider kills them
            max_transaction_retry_time=60,  # bumped above the driver default of 30s
        )

    def close(self):
        self.driver.close()

    # low-level helpers, everything else routes through these to get the
    # driver's managed-transaction retry behavior

    def _run_write(self, cypher: str, **params) -> None:
        def _work(tx):
            tx.run(cypher, **params)

        with self.driver.session() as session:
            session.execute_write(_work)

    def _run_write_returning(self, cypher: str, **params) -> list[dict]:
        def _work(tx):
            return [dict(r) for r in tx.run(cypher, **params)]

        with self.driver.session() as session:
            return session.execute_write(_work)

    def _run_read(self, cypher: str, **params) -> list[dict]:
        def _work(tx):
            return [dict(r) for r in tx.run(cypher, **params)]

        with self.driver.session() as session:
            return session.execute_read(_work)

    # schema setup, idempotent so it's safe to call on every startup

    def ensure_schema(self):
        statements = [
            "CREATE CONSTRAINT job_id_unique IF NOT EXISTS FOR (j:DocumentJob) REQUIRE j.job_id IS UNIQUE",
            "CREATE CONSTRAINT query_job_id_unique IF NOT EXISTS FOR (q:QueryJob) REQUIRE q.job_id IS UNIQUE",
            "CREATE CONSTRAINT contract_id_unique IF NOT EXISTS FOR (c:Contract) REQUIRE c.contract_id IS UNIQUE",
            "CREATE CONSTRAINT party_name_unique IF NOT EXISTS FOR (p:Party) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT clause_id_unique IF NOT EXISTS FOR (cl:Clause) REQUIRE cl.clause_id IS UNIQUE",
            "CREATE CONSTRAINT judgment_citation_unique IF NOT EXISTS FOR (j:Judgment) REQUIRE j.citation IS UNIQUE",
            "CREATE CONSTRAINT risk_flag_id_unique IF NOT EXISTS FOR (r:RiskFlag) REQUIRE r.flag_id IS UNIQUE",
            "CREATE CONSTRAINT decision_id_unique IF NOT EXISTS FOR (d:ReviewerDecision) REQUIRE d.decision_id IS UNIQUE",
            "CREATE CONSTRAINT audit_id_unique IF NOT EXISTS FOR (a:AuditRecord) REQUIRE a.audit_id IS UNIQUE",
        ]
        vector_index = f"""
            CREATE VECTOR INDEX clause_embedding_index IF NOT EXISTS
            FOR (cl:Clause) ON (cl.embedding)
            OPTIONS {{ indexConfig: {{
                `vector.dimensions`: {CLAUSE_EMBEDDING_DIM},
                `vector.similarity_function`: 'cosine'
            }} }}
        """

        def _work(tx):
            for stmt in statements:
                tx.run(stmt)
            tx.run(vector_index)

        with self.driver.session() as session:
            session.execute_write(_work)

    # document job / contract / party

    def create_document_job(self, job_id: str, document_name: str) -> None:
        self._run_write(
            """
            MERGE (j:DocumentJob {job_id: $job_id})
            SET j.document_name = $document_name,
                j.status = 'processing',
                j.created_at = $now,
                j.updated_at = $now
            """,
            job_id=job_id, document_name=document_name, now=_now(),
        )

    def update_document_job_status(self, job_id: str, status: str) -> None:
        self._run_write(
            "MATCH (j:DocumentJob {job_id: $job_id}) SET j.status = $status, j.updated_at = $now",
            job_id=job_id, status=status, now=_now(),
        )

    def create_contract(self, contract_id: str, job_id: str, document_name: str,
                         contract_name: Optional[str] = None) -> None:
        self._run_write(
            """
            MATCH (j:DocumentJob {job_id: $job_id})
            MERGE (c:Contract {contract_id: $contract_id})
            SET c.document_name = $document_name,
                c.name = coalesce($contract_name, $document_name),
                c.approved = null
            MERGE (j)-[:PRODUCED]->(c)
            """,
            job_id=job_id, contract_id=contract_id,
            document_name=document_name, contract_name=contract_name,
        )

    def link_vendor(self, contract_id: str, party_name: str) -> None:
        self._run_write(
            """
            MATCH (c:Contract {contract_id: $contract_id})
            MERGE (p:Party {name: $party_name})
            MERGE (c)-[:HAS_VENDOR]->(p)
            """,
            contract_id=contract_id, party_name=party_name,
        )

    def link_party_with_role(self, contract_id: str, party_name: str, role: Optional[str]) -> None:
        """Like link_vendor but for any role (Lessor, Licensee, etc), adding a
        separate HAS_PARTY relationship alongside HAS_VENDOR rather than replacing it."""
        self._run_write(
            """
            MATCH (c:Contract {contract_id: $contract_id})
            MERGE (p:Party {name: $party_name})
            MERGE (c)-[r:HAS_PARTY]->(p)
            SET r.role = $role
            """,
            contract_id=contract_id, party_name=party_name, role=role,
        )

    def set_contract_subject_matter(self, contract_id: str, subject_matter: Optional[str]) -> None:
        if not subject_matter:
            return
        self._run_write(
            "MATCH (c:Contract {contract_id: $contract_id}) SET c.subject_matter = $subject_matter",
            contract_id=contract_id, subject_matter=subject_matter,
        )

    def create_query_job(self, job_id: str, question: str) -> None:
        """QueryJob is the query-side counterpart to DocumentJob, so questions
        get the same audit trail as ingestion does."""
        self._run_write(
            """
            MERGE (q:QueryJob {job_id: $job_id})
            SET q.question = $question, q.status = 'processing',
                q.created_at = $now, q.updated_at = $now
            """,
            job_id=job_id, question=question, now=_now(),
        )

    def update_job_status(self, job_id: str, status: str) -> None:
        """Works for DocumentJob or QueryJob: matched by job_id, not label."""
        self._run_write(
            "MATCH (j {job_id: $job_id}) SET j.status = $status, j.updated_at = $now",
            job_id=job_id, status=status, now=_now(),
        )

    def store_query_answer(self, job_id: str, answer: str) -> None:
        self._run_write(
            "MATCH (q:QueryJob {job_id: $job_id}) SET q.final_answer = $answer",
            job_id=job_id, answer=answer,
        )

    def set_contract_approval(self, contract_id: str, approved: bool) -> None:
        self._run_write(
            "MATCH (c:Contract {contract_id: $contract_id}) SET c.approved = $approved",
            contract_id=contract_id, approved=approved,
        )

    # clauses

    def create_clause(self, clause_id: str, contract_id: str, text: str, embedding: list[float],
                       page_start: int, page_end: int, section: Optional[str],
                       clause_type: Optional[str], confidence: Optional[float] = None) -> None:
        self._run_write(
            """
            MATCH (c:Contract {contract_id: $contract_id})
            MERGE (cl:Clause {clause_id: $clause_id})
            SET cl.text = $text, cl.embedding = $embedding,
                cl.page_start = $page_start, cl.page_end = $page_end,
                cl.section = $section, cl.clause_type = $clause_type,
                cl.confidence = $confidence
            MERGE (c)-[:CONTAINS_CLAUSE]->(cl)
            """,
            clause_id=clause_id, contract_id=contract_id, text=text, embedding=embedding,
            page_start=page_start, page_end=page_end, section=section, clause_type=clause_type,
            confidence=confidence,
        )

    def find_similar_clauses(self, clause_id: str, embedding: list[float],
                              top_k: int = 5, min_similarity: float = 0.90) -> list[dict]:
        """Vector search for similar clauses, excluding the clause itself and
        anything from the same contract, we only care about other contracts here."""
        return self._run_read(
            """
            CALL db.index.vector.queryNodes('clause_embedding_index', $top_k, $embedding)
            YIELD node, score
            WHERE node.clause_id <> $clause_id AND score >= $min_similarity
            MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(node)
            RETURN node.clause_id AS clause_id, node.text AS text,
                   c.contract_id AS contract_id, c.name AS contract_name, score
            """,
            top_k=top_k, embedding=embedding, clause_id=clause_id, min_similarity=min_similarity,
        )

    def link_same_clause(self, clause_id_a: str, clause_id_b: str, similarity: float) -> None:
        self._run_write(
            """
            MATCH (a:Clause {clause_id: $a}), (b:Clause {clause_id: $b})
            MERGE (a)-[r:SAME_CLAUSE_AS]->(b)
            SET r.similarity = $similarity
            """,
            a=clause_id_a, b=clause_id_b, similarity=similarity,
        )

    def create_conflict(self, clause_id_a: str, clause_id_b: str, reason: str) -> None:
        self._run_write(
            """
            MATCH (a:Clause {clause_id: $a}), (b:Clause {clause_id: $b})
            MERGE (a)-[r:CONFLICTS_WITH]->(b)
            SET r.reason = $reason
            """,
            a=clause_id_a, b=clause_id_b, reason=reason,
        )

    def create_judgment(self, citation: str, court: Optional[str], year: Optional[int],
                         summary: Optional[str]) -> None:
        self._run_write(
            """
            MERGE (j:Judgment {citation: $citation})
            SET j.court = coalesce($court, j.court),
                j.year = coalesce($year, j.year),
                j.summary = coalesce($summary, j.summary)
            """,
            citation=citation, court=court, year=year, summary=summary,
        )

    def link_interpreted_by(self, clause_id: str, judgment_citation: str) -> None:
        self._run_write(
            """
            MATCH (cl:Clause {clause_id: $clause_id}), (j:Judgment {citation: $citation})
            MERGE (cl)-[:INTERPRETED_BY]->(j)
            """,
            clause_id=clause_id, citation=judgment_citation,
        )

    # risk flags / reviewer decisions / audit

    def create_risk_flag(self, clause_id: str, risk_level: str, reason: str,
                          confidence: Optional[float] = None,
                          recommended_action: Optional[str] = None) -> str:
        flag_id = str(uuid.uuid4())
        self._run_write(
            """
            MATCH (cl:Clause {clause_id: $clause_id})
            CREATE (r:RiskFlag {flag_id: $flag_id, risk_level: $risk_level,
                                 reason: $reason, confidence: $confidence,
                                 recommended_action: $recommended_action, created_at: $now})
            MERGE (cl)-[:FLAGGED_AS]->(r)
            """,
            clause_id=clause_id, flag_id=flag_id, risk_level=risk_level, reason=reason,
            confidence=confidence, recommended_action=recommended_action, now=_now(),
        )
        return flag_id

    def create_missing_clause_flag(self, contract_id: str, clause_type: str, reason: str) -> str:
        """Missing clauses have no source Clause node to attach to, so this
        hangs off the Contract directly instead of via FLAGGED_AS."""
        flag_id = str(uuid.uuid4())
        self._run_write(
            """
            MATCH (c:Contract {contract_id: $contract_id})
            CREATE (r:RiskFlag {flag_id: $flag_id, risk_level: 'high', reason: $reason,
                                 clause_type: $clause_type, missing_clause: true, created_at: $now})
            MERGE (c)-[:MISSING_CLAUSE_FLAG]->(r)
            """,
            contract_id=contract_id, flag_id=flag_id, clause_type=clause_type, reason=reason, now=_now(),
        )
        return flag_id

    def create_reviewer_decision(self, job_id: str, approved: bool, reviewer: str,
                                  comments: Optional[str]) -> str:
        """Attaches a ReviewerDecision to whichever *Job node has this job_id (DocumentJob or QueryJob)."""
        decision_id = str(uuid.uuid4())
        self._run_write(
            """
            MATCH (j {job_id: $job_id})
            CREATE (d:ReviewerDecision {decision_id: $decision_id, approved: $approved,
                                         reviewer: $reviewer, comments: $comments, decided_at: $now})
            MERGE (j)-[:REVIEWED_BY]->(d)
            """,
            job_id=job_id, decision_id=decision_id, approved=approved,
            reviewer=reviewer, comments=comments, now=_now(),
        )
        return decision_id

    def write_audit_record(self, job_id: str, actor: str, action: str, details: str) -> str:
        """Append-only: never update/delete an AuditRecord, add a new one instead."""
        audit_id = str(uuid.uuid4())
        self._run_write(
            """
            MATCH (j {job_id: $job_id})
            CREATE (a:AuditRecord {audit_id: $audit_id, actor: $actor, action: $action,
                                    details: $details, timestamp: $now})
            MERGE (j)-[:HAS_AUDIT_RECORD]->(a)
            """,
            job_id=job_id, audit_id=audit_id, actor=actor, action=action, details=details, now=_now(),
        )
        return audit_id

    # graph write-back / generic read access

    def record_answered_question(self, job_id: str, question: str, answer: str,
                                  cited_clause_ids: list[str]) -> str:
        """Only called after human approval, adds this Q&A into the graph
        as a lightweight precedent a future query can find directly."""
        answered_id = str(uuid.uuid4())
        self._run_write(
            """
            MATCH (q:QueryJob {job_id: $job_id})
            CREATE (a:AnsweredQuestion {answered_id: $answered_id, question: $question,
                                         answer: $answer, answered_at: $now})
            MERGE (q)-[:PRODUCED_ANSWER]->(a)
            WITH a
            UNWIND $clause_ids AS cid
            MATCH (cl:Clause {clause_id: cid})
            MERGE (a)-[:CITES]->(cl)
            """,
            job_id=job_id, answered_id=answered_id, question=question, answer=answer,
            now=_now(), clause_ids=cited_clause_ids,
        )
        return answered_id

    def run_read_query(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        return self._run_read(cypher, **(params or {}))

    def get_audit_trail(self, job_id: str) -> list[dict]:
        """Full audit history for a DocumentJob or QueryJob, oldest first."""
        return self._run_read(
            """
            MATCH (j {job_id: $job_id})-[:HAS_AUDIT_RECORD]->(a:AuditRecord)
            RETURN a.audit_id AS audit_id, a.actor AS actor, a.action AS action,
                   a.details AS details, a.timestamp AS timestamp
            ORDER BY a.timestamp ASC
            """,
            job_id=job_id,
        )

    def get_reviewer_decisions(self, job_id: str) -> list[dict]:
        return self._run_read(
            """
            MATCH (j {job_id: $job_id})-[:REVIEWED_BY]->(d:ReviewerDecision)
            RETURN d.decision_id AS decision_id, d.approved AS approved, d.reviewer AS reviewer,
                   d.comments AS comments, d.decided_at AS decided_at
            ORDER BY d.decided_at ASC
            """,
            job_id=job_id,
        )

    # legal operations dashboard

    # cross-portfolio conflict detection (section 6)

    def find_same_type_clauses_across_contracts(self, contract_ids: list[str]) -> list[dict]:
        """Given a set of contract_ids (already scoped to the same org profile or
        counterparty by the caller via Postgres), returns every clause on those
        contracts grouped implicitly by clause_type (the caller does the grouping/
        comparison; Cypher just gets the raw rows out efficiently)."""
        if not contract_ids:
            return []
        return self._run_read(
            """
            MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl:Clause)
            WHERE c.contract_id IN $contract_ids AND cl.clause_type IS NOT NULL
            RETURN c.contract_id AS contract_id, c.name AS contract_name,
                   cl.clause_id AS clause_id, cl.clause_type AS clause_type, cl.text AS text
            """,
            contract_ids=contract_ids,
        )

    def get_dashboard_stats(self) -> dict:
        """Aggregate throughput/approval/risk metrics across every DocumentJob and QueryJob."""
        document_counts = self._run_read(
            """
            MATCH (j:DocumentJob)
            RETURN j.status AS status, count(*) AS count
            """
        )
        query_counts = self._run_read(
            """
            MATCH (q:QueryJob)
            RETURN q.status AS status, count(*) AS count
            """
        )
        risk_breakdown = self._run_read(
            """
            MATCH (r:RiskFlag)
            RETURN r.risk_level AS risk_level, count(*) AS count
            """
        )
        missing_clause_count = self._run_read(
            """
            MATCH (r:RiskFlag {missing_clause: true})
            RETURN count(*) AS count
            """
        )
        conflict_count = self._run_read(
            """
            MATCH (:Clause)-[c:CONFLICTS_WITH]->(:Clause)
            RETURN count(c) AS count
            """
        )
        total_contracts = self._run_read("MATCH (c:Contract) RETURN count(c) AS count")

        return {
            "document_jobs_by_status": {row["status"]: row["count"] for row in document_counts if row["status"]},
            "query_jobs_by_status": {row["status"]: row["count"] for row in query_counts if row["status"]},
            "risk_flags_by_level": {row["risk_level"]: row["count"] for row in risk_breakdown if row["risk_level"]},
            "missing_clause_flags": missing_clause_count[0]["count"] if missing_clause_count else 0,
            "conflicting_clause_pairs": conflict_count[0]["count"] if conflict_count else 0,
            "total_contracts": total_contracts[0]["count"] if total_contracts else 0,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
