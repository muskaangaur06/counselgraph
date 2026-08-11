"""
LLM helpers for the GraphRAG pipeline - clause extraction, conflict detection,
risk flagging. Each is its own narrow call and everything expects JSON back.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..llm_client import call_json, call_text
from ..guardrails import wrap_untrusted


# text-to-cypher (query side), with a read-only safety guard

GRAPH_SCHEMA_DESCRIPTION = """
Nodes:
  (:DocumentJob {job_id, document_name, status})
  (:Contract {contract_id, name, document_name, approved})
  (:Party {name})
  (:Clause {clause_id, text, page_start, page_end, section, clause_type})
  (:Judgment {citation, court, year, summary})
  (:RiskFlag {flag_id, risk_level, reason})

Relationships:
  (:DocumentJob)-[:PRODUCED]->(:Contract)
  (:Contract)-[:HAS_VENDOR]->(:Party)
  (:Contract)-[:CONTAINS_CLAUSE]->(:Clause)
  (:Clause)-[:SAME_CLAUSE_AS {similarity}]->(:Clause)
  (:Clause)-[:CONFLICTS_WITH {reason}]->(:Clause)
  (:Clause)-[:INTERPRETED_BY]->(:Judgment)
  (:Clause)-[:FLAGGED_AS]->(:RiskFlag)
"""

_FORBIDDEN_CYPHER_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL\s+apoc\.|LOAD\s+CSV)\b", re.IGNORECASE
)


def is_read_only_cypher(cypher: str) -> bool:
    """Rejects any generated Cypher that could mutate the graph."""
    return not _FORBIDDEN_CYPHER_KEYWORDS.search(cypher)


_CYPHER_SYSTEM_PROMPT = f"""You translate legal/contract questions into a single \
read-only Cypher query for a Neo4j graph with this schema:
{GRAPH_SCHEMA_DESCRIPTION}

Rules:
- Output ONLY the Cypher query: no prose, no markdown fences, no explanation.
- Use only MATCH / OPTIONAL MATCH / WHERE / WITH / RETURN / ORDER BY / LIMIT / UNWIND.
- Never use CREATE, MERGE, DELETE, SET, REMOVE, or DROP.
- Always RETURN properties needed to cite the source (contract name, clause text or id,
  judgment citation), not whole nodes.
"""


def generate_cypher(question: str) -> str:
    # read-only guard below is the actual protection; wrapping is just extra defense
    cypher = call_text(_CYPHER_SYSTEM_PROMPT, wrap_untrusted("user_question", question))
    cypher = re.sub(r"^```(cypher)?|```$", "", cypher.strip(), flags=re.MULTILINE).strip()
    if not is_read_only_cypher(cypher):
        raise ValueError(f"Generated Cypher failed the read-only safety check:\n{cypher}")
    return cypher


# answer synthesis from combined vector-search + graph-query evidence

_SYNTHESIS_SYSTEM_PROMPT = """You are a legal research assistant. Answer the \
user's question using ONLY the evidence provided below (vector-search hits \
from the document text, and results from a graph query over contracts, \
clauses, and judgments). Cite specific contracts, clause text/ids, and \
judgment citations for every claim. If the evidence does not fully answer \
the question, say so explicitly rather than speculating."""


def synthesize_answer(question: str, vector_hits: list[dict], graph_hits: list[dict]) -> str:
    evidence = {
        "vector_search_results": vector_hits,
        "graph_query_results": graph_hits,
    }
    user_prompt = f"Question: {question}\n\nEvidence (JSON):\n{json.dumps(evidence, default=str, indent=2)}"
    return call_text(_SYNTHESIS_SYSTEM_PROMPT, user_prompt, max_tokens=1200)


# clause + inline judgment-citation extraction

_CLAUSE_SYSTEM_PROMPT = """You are a legal document analyst. Given a chunk of \
contract text, extract each distinct clause it contains.

Respond with ONLY a JSON array (no prose, no markdown fences). Each element:
{
  "clause_type": string,          // e.g. "termination", "governing_law", "indemnification", "confidentiality", "liability_cap", "other"
  "text": string,                 // the clause text, verbatim from the input
  "parties_mentioned": [string],  // any party/company names mentioned in this clause
  "confidence": number,           // 0.0-1.0, how confident you are this is a correctly typed, complete clause
  "judgment_citations": [         // any court judgments explicitly cited in this clause, else []
    {"citation": string, "court": string|null, "year": integer|null}
  ]
}
If the chunk contains no identifiable clauses, return []."""


def extract_clauses(chunk_text: str) -> list[dict]:
    # chunk_text is from an uploaded PDF, treat as untrusted input
    result = call_json(_CLAUSE_SYSTEM_PROMPT, wrap_untrusted("document_text", chunk_text))
    return result if isinstance(result, list) else []


# conflict detection across a document's own clauses

_CONFLICT_SYSTEM_PROMPT = """You are a legal document analyst. Given a JSON \
array of clauses (each with an "id" and "text") from the SAME contract, \
identify pairs that directly contradict each other (e.g. two different \
governing-law jurisdictions, inconsistent notice periods, conflicting \
liability caps).

Respond with ONLY a JSON array (no prose, no markdown fences):
[{"clause_id_a": string, "clause_id_b": string, "reason": string}, ...]
If there are no conflicts, return []."""


def detect_conflicts(clauses: list[dict]) -> list[dict]:
    """clauses: [{"id", "text"}, ...] for one contract. Returns
    [{"clause_id_a", "clause_id_b", "reason"}, ...]."""
    if len(clauses) < 2:
        return []
    payload = json.dumps([{"id": c["id"], "text": c["text"]} for c in clauses])
    result = call_json(_CONFLICT_SYSTEM_PROMPT, payload)
    return result if isinstance(result, list) else []


# risk flagging

_RISK_SYSTEM_PROMPT = """You are a contract risk reviewer. Given a JSON array \
of clauses (each with an "id", "clause_type", and "text"), assign a risk \
level to each.

Respond with ONLY a JSON array (no prose, no markdown fences):
[{"clause_id": string, "risk_level": "low"|"medium"|"high", "reason": string,
  "confidence": number, "recommended_action": string}, ...]
"confidence" is 0.0-1.0, how confident you are in this risk assessment.
"recommended_action" is a short instruction for the reviewer (e.g. "Escalate to senior counsel",
"Flag for legal review", "Note for reviewer awareness").
Only include clauses that carry at least low risk worth a reviewer's attention;
omit clauses with no notable risk."""


def flag_risks(clauses: list[dict]) -> list[dict]:
    """clauses: [{"id", "clause_type", "text"}, ...]. Returns
    [{"clause_id", "risk_level", "reason", "confidence", "recommended_action"}, ...]."""
    if not clauses:
        return []
    payload = json.dumps([{"id": c["id"], "clause_type": c.get("clause_type"), "text": c["text"]} for c in clauses])
    result = call_json(_RISK_SYSTEM_PROMPT, payload)
    return result if isinstance(result, list) else []


# missing-clause detection against an expected-clause checklist
#
# The checklist used to be a hardcoded global dict here. It now lives in Postgres
# on each org_profile's required_clause_checklist (keyed by contract_type), so
# different business units (e.g. "Vendor Procurement Unit" vs "Cross-Border
# Compliance Unit") can have different expectations for the same contract_type.
# _FALLBACK_EXPECTED_CLAUSES_BY_CONTRACT_TYPE only exists so this module still works
# standalone (e.g. tests/eval/run_eval.py, which has no org_profile_id to resolve).

_FALLBACK_EXPECTED_CLAUSES_BY_CONTRACT_TYPE = {
    "outsourcing": ["termination", "liability_cap", "confidentiality", "indemnification", "governing_law"],
    "service": ["termination", "liability_cap", "confidentiality", "governing_law"],
    "supply": ["termination", "liability_cap", "confidentiality", "governing_law"],
    "maintenance": ["termination", "liability_cap", "confidentiality", "governing_law"],
    "license agreement": ["termination", "confidentiality", "governing_law"],
    "non-compete/solicit": ["confidentiality", "termination", "governing_law"],
}
_DEFAULT_EXPECTED_CLAUSES = ["termination", "liability_cap", "confidentiality", "governing_law"]


def _fallback_expected_clauses(contract_type: Optional[str]) -> list[str]:
    return _FALLBACK_EXPECTED_CLAUSES_BY_CONTRACT_TYPE.get(
        (contract_type or "").strip().lower(), _DEFAULT_EXPECTED_CLAUSES
    )


def detect_missing_clauses(contract_type: Optional[str], present_clause_types: list[str],
                            org_profile_id: Optional[str] = None) -> list[dict]:
    """Compares extracted clause types against an expected-clause checklist for the
    document's contract type, resolved from the org profile in Postgres when
    org_profile_id is given. Falls back to a small built-in checklist otherwise
    (e.g. when called standalone with no org profile context, as in the eval script).
    Returns [{"clause_type", "reason"}, ...] for anything absent."""
    if org_profile_id:
        try:
            from ..db.repository import get_expected_clauses
            expected = get_expected_clauses(org_profile_id, contract_type)
        except Exception:
            expected = _fallback_expected_clauses(contract_type)
    else:
        expected = _fallback_expected_clauses(contract_type)

    present = {c.lower() for c in present_clause_types if c}
    return [
        {
            "clause_type": expected_type,
            "reason": f"No {expected_type.replace('_', ' ')} clause was found in this document, "
                      f"but it is expected for this contract type.",
        }
        for expected_type in expected
        if expected_type not in present
    ]
