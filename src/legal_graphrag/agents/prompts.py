"""
LLM logic for the router, auditor, and synthesizer sub-agents. Separate from
graphrag/extraction.py since that's ingestion-time, this is query-time.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from ..llm_client import call_json, call_text  # call_text kept for any future plain-text needs
from ..guardrails import wrap_untrusted

# Structured output models

class RouterDecision(BaseModel):
    routes: list[str] = Field(default_factory=lambda: ["hybrid"])
    query_style: str = "balanced"
    reasoning: str = ""


class EvidenceVerdict(BaseModel):
    sufficient: bool
    reasoning: str
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class SynthesizedAnswer(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
    risk_level: Optional[str] = None          # "low" | "medium" | "high" | None
    has_uncertainty: bool = False


# query-style classification -> hybrid-search blend weight (alpha)
# picking alpha per-query here since the router is already classifying anyway

ALPHA_BY_QUERY_STYLE = {
    "exact_match": 0.2,   # favor lexical/BM25: looking for specific wording, a clause number, a citation
    "balanced": 0.4,      # slight lexical lean: legal text still rewards exact term matching most of the time
    "semantic": 0.7,      # favor dense: open-ended "what happens if / explain / summarize" questions
}
DEFAULT_ALPHA = ALPHA_BY_QUERY_STYLE["balanced"]

_EXACT_MATCH_PATTERNS = [
    re.compile(r'"[^"]{3,}"'),
    re.compile(r"\bsection\s+\d", re.IGNORECASE),
    re.compile(r"\bclause\s+\d", re.IGNORECASE),
    re.compile(r"\b(art\.|article)\s+\d", re.IGNORECASE),
    re.compile(r"\bv\.\s+[A-Z]"),
]
_SEMANTIC_KEYWORDS = [
    "why", "explain", "what happens if", "summarize", "summarise", "in plain english",
    "does this mean", "how does", "what if", "implications", "risk of",
]

# whole-document requests: broad enough that similarity search has nothing
# specific to rank chunks against, so the whole document should be pulled
# instead of doing a hybrid/vector search
_WHOLE_DOCUMENT_KEYWORDS = [
    "summarize it", "summarise it", "summarize this", "summarise this",
    "summarize the document", "summarise the document", "summarize the contract",
    "summarise the contract", "summarize the agreement", "summarise the agreement",
    "give me a summary", "overview of this", "overview of the document",
    "overview of the contract", "key terms of this", "key points of this",
    "walk me through this", "what does this contract say", "what does this say overall",
]


def is_whole_document_request(question: str) -> bool:
    """Broad, unspecific "summarize this whole thing" requests: no clause type,
    no specific term, nothing for retrieval to rank chunks against."""
    q_lower = question.lower().strip()
    if any(kw in q_lower for kw in _WHOLE_DOCUMENT_KEYWORDS):
        return True
    # extremely short generic asks like "summarize it please" or "summary?"
    generic_short = re.sub(r"[^a-z\s]", "", q_lower)
    words = generic_short.split()
    return len(words) <= 5 and any(w.startswith("summar") or w == "overview" for w in words)


def classify_query_style(question: str) -> str:
    """Keyword heuristics only, no LLM call."""
    if any(p.search(question) for p in _EXACT_MATCH_PATTERNS):
        return "exact_match"
    q_lower = question.lower()
    if any(kw in q_lower for kw in _SEMANTIC_KEYWORDS):
        return "semantic"
    return "balanced"


# router: classifies a question into one or more retrieval paths, plus the
# blend weight to use if "hybrid" is one of them. Some questions need both
# specialists at once (e.g. "indemnification clauses for Vendor X" needs graph
# traversal to find Vendor X's contracts, then semantic search within them).

_GRAPH_KEYWORDS = [
    "same clause", "another contract", "other contract", "conflict", "conflicts",
    "judgment", "judgement", "precedent", "interpreted by", "relationship between",
    "across contracts", "multiple contracts", "linked to", "connected to",
    "cites", "citing", "who else", "vendor", "counterparty", "party to",
]

# if both a graph keyword and a clause-topic keyword show up, probably needs
# both specialists. left out generic words like "clause"/"provision" since
# those show up in almost any graph question and would over-trigger this.
_CLAUSE_TOPIC_KEYWORDS = [
    "indemnification", "indemnity", "termination", "confidentiality", "liability",
    "non-compete", "non compete", "warranty", "warranties",
    "governing law", "limitation of liability",
]

_ROUTER_SYSTEM_PROMPT = """You classify a legal-document question along two \
independent dimensions.

1. Retrieval path(s): one or two of:
- "hybrid": finding/quoting specific clause text, a policy, a defined term, \
or searching contracts by attributes (party, date, value, type).
- "graph": traversing RELATIONSHIPS between entities, such as the same clause in \
multiple contracts, conflicting clauses, clauses interpreted by judgments, \
or multi-hop chains between contracts/parties/precedents.
- "direct": a simple summarization/explanation/follow-up about evidence \
ALREADY gathered in this conversation, no new retrieval at all.
Return TWO paths ("hybrid" AND "graph") only when the question genuinely \
needs both, e.g. "find clause type X in contracts involving party Y" needs \
graph traversal to find party Y's contracts AND semantic search within them \
for clause type X. "direct" is never combined with another path.

2. Query style (only meaningful if "hybrid" is one of the paths): exactly one of:
- "exact_match": looking for specific wording, a clause number, a defined \
term, or a citation.
- "semantic": open-ended, conceptual, or paraphrase-style question.
- "balanced": neither clearly dominates.

Respond with ONLY a JSON object:
{"routes": ["hybrid"|"graph"|"direct", ...], "query_style": "exact_match"|"semantic"|"balanced", "reasoning": string}"""


def _validated_router_decision(question: str) -> RouterDecision:
    raw = call_json(_ROUTER_SYSTEM_PROMPT, wrap_untrusted("user_question", question))
    try:
        decision = RouterDecision.model_validate(raw)
    except ValidationError:
        return RouterDecision(routes=["hybrid"], query_style="balanced",
                               reasoning="router LLM output failed validation; defaulting to hybrid search")

    # "direct" never combines with anything else, and only known routes survive.
    routes = [r for r in decision.routes if r in ("hybrid", "graph", "direct")] or ["hybrid"]
    if "direct" in routes and len(routes) > 1:
        routes = [r for r in routes if r != "direct"]
    decision.routes = routes
    return decision


def classify_routes(question: str) -> tuple[list[str], str, float]:
    """alpha only matters when "hybrid" is one of the routes, but we always return it."""
    q_lower = question.lower()
    style = classify_query_style(question)
    alpha = ALPHA_BY_QUERY_STYLE[style]

    if is_whole_document_request(question):
        return ["whole_document"], "broad summary-style request, no specific term to search against", alpha

    graph_match = any(kw in q_lower for kw in _GRAPH_KEYWORDS)
    hybrid_topic_match = any(kw in q_lower for kw in _CLAUSE_TOPIC_KEYWORDS)

    if graph_match and hybrid_topic_match:
        return ["hybrid", "graph"], "matched both a relationship keyword and a clause-topic keyword", alpha
    if graph_match:
        return ["graph"], "matched a relationship/multi-hop keyword pattern", alpha
    if style != "balanced":
        # confident enough on keywords alone, skip the LLM call
        return ["hybrid"], f"keyword-matched query style: {style}", alpha

    # ambiguous case, ask the LLM for routes + style together
    decision = _validated_router_decision(question)
    alpha = ALPHA_BY_QUERY_STYLE.get(decision.query_style, DEFAULT_ALPHA)
    return decision.routes, decision.reasoning, alpha


def classify_route(question: str) -> tuple[str, str, float]:
    """Old single-route wrapper, kept for backward compat. Prefer classify_routes()."""
    routes, reasoning, alpha = classify_routes(question)
    return routes[0], reasoning, alpha


# auditor: verifies retrieved evidence before synthesis

_AUDITOR_SYSTEM_PROMPT = """You are an evidence auditor for a legal research \
system. You do NOT answer the question. You only assess whether the \
evidence provided is sufficient, relevant, and internally consistent \
enough to answer it responsibly.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "sufficient": boolean,
  "reasoning": string,
  "gaps": [string],
  "contradictions": [string]
}"""


def verify_evidence(question: str, hybrid_hits: list[dict], graph_hits: list[dict]) -> dict:
    evidence = {"hybrid_search_results": hybrid_hits, "graph_query_results": graph_hits}
    user_prompt = f"Question: {question}\n\nEvidence (JSON):\n{json.dumps(evidence, default=str, indent=2)}"
    raw = call_json(_AUDITOR_SYSTEM_PROMPT, user_prompt)
    try:
        verdict = EvidenceVerdict.model_validate(raw)
    except ValidationError as e:
        verdict = EvidenceVerdict(
            sufficient=False,
            reasoning=f"auditor response failed validation ({e.error_count()} error(s)); treating as insufficient",
        )
    return verdict.model_dump()


# synthesizer: writes the final answer from verified evidence only

_SYNTHESIZER_SYSTEM_PROMPT = """You are a legal research assistant writing \
the FINAL answer for a human reviewer. Use ONLY the evidence provided, \
never speculate beyond it.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "answer": string,              // clear prose: summary, risk explanation if relevant, and
                                  // explicit citations to specific contracts/clauses/judgments
  "citations": [string],         // short citation strings pulled out of the answer, e.g.
                                  // "Contract ABC MSA, Clause 4.2", "Smith v. Jones (2019)"
  "risk_level": "low"|"medium"|"high"|null,   // overall risk the evidence reveals, or null if not applicable
  "has_uncertainty": boolean     // true if the evidence auditor flagged gaps/contradictions
                                  // that meaningfully limit confidence in this answer
}

Explicitly reflect any gaps or contradictions the evidence auditor flagged, \
do not paper over them. Do not include markdown formatting inside "answer"; \
plain prose only."""


def synthesize_legal_answer(question: str, hybrid_hits: list[dict], graph_hits: list[dict],
                             evidence_verdict: dict) -> dict:
    evidence = {
        "hybrid_search_results": hybrid_hits,
        "graph_query_results": graph_hits,
        "evidence_auditor_verdict": evidence_verdict,
    }
    user_prompt = f"Question: {question}\n\nVerified evidence (JSON):\n{json.dumps(evidence, default=str, indent=2)}"
    raw = call_json(_SYNTHESIZER_SYSTEM_PROMPT, user_prompt, max_tokens=1500)
    try:
        result = SynthesizedAnswer.model_validate(raw)
    except ValidationError:
        # fallback is still a validated SynthesizedAnswer, so callers don't
        # need to guard against a missing "answer" key
        result = SynthesizedAnswer(
            answer="The system was unable to generate a structured answer from the retrieved evidence. "
                   "Please review the raw evidence directly.",
            has_uncertainty=True,
        )
    return result.model_dump()


# revision: takes reviewer feedback on a draft answer and produces a revised
# one, using the same verified evidence rather than going back to retrieval

_REVISION_SYSTEM_PROMPT = """You are a legal research assistant revising a \
previously drafted answer based on feedback from a human reviewer. Use \
ONLY the evidence already provided, do not invent new evidence or claims \
that aren't grounded in it. Directly address the reviewer's feedback: \
correct what they flagged, clarify what they found unclear, or add detail \
where they asked for more, while keeping everything else grounded in the \
same verified evidence as the previous answer.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "answer": string,
  "citations": [string],
  "risk_level": "low"|"medium"|"high"|null,
  "has_uncertainty": boolean
}"""


def revise_legal_answer(question: str, previous_answer: str, hybrid_hits: list[dict],
                         graph_hits: list[dict], evidence_verdict: dict,
                         reviewer_feedback: str) -> dict:
    payload = {
        "previous_answer": previous_answer,
        "reviewer_feedback": reviewer_feedback,
        "hybrid_search_results": hybrid_hits,
        "graph_query_results": graph_hits,
        "evidence_auditor_verdict": evidence_verdict,
    }
    user_prompt = f"Question: {question}\n\nContext for revision (JSON):\n{json.dumps(payload, default=str, indent=2)}"
    raw = call_json(_REVISION_SYSTEM_PROMPT, user_prompt, max_tokens=1500)
    try:
        result = SynthesizedAnswer.model_validate(raw)
    except ValidationError:
        # keep the previous answer unchanged rather than risk a malformed revision
        result = SynthesizedAnswer(answer=previous_answer, has_uncertainty=True)
    return result.model_dump()
