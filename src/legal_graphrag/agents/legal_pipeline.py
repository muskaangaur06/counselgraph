"""
Main LangGraph pipeline for the legal assistant: router -> retrieval agent ->
auditor -> synthesizer, with two human-in-the-loop checkpoints (evidence
review before generation, then approve/revise/reject on the drafted answer).
Revisions loop back to the same checkpoint, and Neo4j only gets written to
once an answer is actually approved.
"""

from __future__ import annotations

import uuid
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.types import Command, interrupt

from ..resources import get_store, get_checkpointer
from ..graphrag.extraction import generate_cypher
from ..graphrag.langgraph_agent import match_template  # vendor/same-clause/judgments template
from ..retrieval.hybrid_search import hybrid_search, get_full_document_text
from ..retrieval.contract_metadata import generate_document_summary
from .prompts import (
    classify_route,
    verify_evidence,
    synthesize_legal_answer,
    revise_legal_answer,
    DEFAULT_ALPHA,
)


# shared graph state
class LegalAgentState(TypedDict, total=False):
    # --- input ---
    question: str
    collection_name: str
    metadata_filter: Optional[dict]

    # --- router ---
    query_job_id: str
    route: str                      # "hybrid" | "graph" | "direct"
    route_reasoning: str
    alpha: float                    # dense/sparse blend weight for HybridSearchAgent, chosen by the router

    # --- specialist retrieval ---
    hybrid_hits: list[dict]
    graph_hits: list[dict]
    cypher_used: Optional[str]
    cypher_source: Optional[str]    # "template" or "generated"

    # --- verification ---
    evidence_verdict: dict
    evidence_decision: dict         # human decision at the evidence checkpoint

    # --- synthesis ---
    draft_answer: str
    draft_citations: list[str]
    draft_risk_level: Optional[str]
    draft_has_uncertainty: bool
    answer_decision: dict           # human decision at the answer checkpoint (this round)
    answer_revision_count: int      # how many "revise" rounds have happened so far
    max_answer_revisions: int       # safety valve: force answer_rejected past this many rounds

    # --- output ---
    final_answer: Optional[str]
    status: str


DEFAULT_MAX_ANSWER_REVISIONS = 3


# job bootstrap
def start_job_node(state: LegalAgentState) -> dict:
    store = get_store()
    job_id = str(uuid.uuid4())
    store.create_query_job(job_id, state["question"])
    store.write_audit_record(job_id, "system", "query_received", state["question"])
    print(f"[start_job] job_id={job_id}")
    return {
        "query_job_id": job_id,
        # defaults so a skipped path doesn't cause a KeyError downstream
        "hybrid_hits": state.get("hybrid_hits", []),
        "graph_hits": state.get("graph_hits", []),
        "cypher_used": None,
        "cypher_source": None,
        "answer_revision_count": 0,
        "max_answer_revisions": state.get("max_answer_revisions", DEFAULT_MAX_ANSWER_REVISIONS),
    }


# router agent
def router_node(state: LegalAgentState) -> dict:
    route, reasoning, alpha = classify_route(state["question"])
    store = get_store()
    store.write_audit_record(
        state["query_job_id"], "router", "route_selected",
        f"route={route} alpha={alpha}: {reasoning}",
    )
    print(f"[router] route={route} alpha={alpha} ({reasoning})")
    return {"route": route, "route_reasoning": reasoning, "alpha": alpha}


def route_after_router(state: LegalAgentState) -> str:
    return {
        "hybrid": "hybrid_search_agent",
        "graph": "graph_rag_agent",
        "direct": "auditor",
        "whole_document": "whole_document_agent",
    }[state["route"]]


# dense + lexical retrieval, good for clause text and citation matching
def hybrid_search_agent_node(state: LegalAgentState) -> dict:
    hits = hybrid_search(
        collection_name=state["collection_name"],
        query=state["question"],
        metadata_filter=state.get("metadata_filter"),
        alpha=state.get("alpha", DEFAULT_ALPHA),
    )
    store = get_store()
    store.write_audit_record(
        state["query_job_id"], "hybrid_search_agent", "retrieval_completed",
        f"hits={len(hits)} alpha={state.get('alpha', DEFAULT_ALPHA)}",
    )
    print(f"[hybrid_search_agent] {len(hits)} hits (alpha={state.get('alpha', DEFAULT_ALPHA)})")
    return {"hybrid_hits": hits}


# whole-document requests ("summarize this"): no specific term to rank chunks
# against, so pull every chunk instead of doing similarity search
def whole_document_agent_node(state: LegalAgentState) -> dict:
    full_text = get_full_document_text(state["collection_name"])
    store = get_store()
    store.write_audit_record(
        state["query_job_id"], "whole_document_agent", "retrieval_completed",
        f"chars={len(full_text)}",
    )
    print(f"[whole_document_agent] pulled {len(full_text)} chars")
    return {"hybrid_hits": [{"text": full_text, "source": "whole_document"}]}


# graph traversal for multi-hop questions (precedent chains, cross-contract matches, etc)
def graph_rag_agent_node(state: LegalAgentState) -> dict:
    store = get_store()
    template_match = match_template(state["question"])

    if template_match:
        cypher, params = template_match
        source = "template"
    else:
        cypher = generate_cypher(state["question"])  # raises if the read-only guard rejects it
        params = {}
        source = "generated"

    hits = store.run_read_query(cypher, params)
    store.write_audit_record(
        state["query_job_id"], "graph_rag_agent", "retrieval_completed",
        f"source={source} hits={len(hits)}",
    )
    print(f"[graph_rag_agent] source={source} hits={len(hits)}")
    return {"cypher_used": cypher, "cypher_source": source, "graph_hits": hits}


# checks evidence quality before we let the LLM generate anything
def auditor_node(state: LegalAgentState) -> dict:
    verdict = verify_evidence(state["question"], state["hybrid_hits"], state["graph_hits"])
    store = get_store()
    store.write_audit_record(
        state["query_job_id"], "auditor", "evidence_verified",
        f"sufficient={verdict.get('sufficient')}: {verdict.get('reasoning', '')}",
    )
    print(f"[auditor] sufficient={verdict.get('sufficient')}")
    return {"evidence_verdict": verdict}


def human_evidence_checkpoint_node(state: LegalAgentState) -> dict:
    """First checkpoint: a reviewer looks at the auditor's verdict and the raw
    evidence and decides if it's good enough to generate an answer from."""
    payload = {
        "type": "evidence_approval_request",
        "query_job_id": state["query_job_id"],
        "question": state["question"],
        "route": state["route"],
        "evidence_verdict": state["evidence_verdict"],
        "hybrid_hits": state["hybrid_hits"],
        "graph_hits": state["graph_hits"],
        "message": "Resume with {'proceed': bool, 'reviewer': str, 'comments': str|None, "
                    "'escalate': bool|None}. Setting 'escalate' true instead of proceeding "
                    "routes this to senior review rather than a plain rejection.",
    }
    decision = interrupt(payload)
    return {"evidence_decision": decision}


def route_after_evidence_checkpoint(state: LegalAgentState) -> str:
    decision = state["evidence_decision"]
    if decision.get("proceed"):
        return "synthesizer"
    if decision.get("escalate"):
        return "evidence_escalated"
    return "evidence_rejected"


def evidence_rejected_node(state: LegalAgentState) -> dict:
    """Evidence got rejected, so we stop before generating anything."""
    store = get_store()
    decision = state["evidence_decision"]
    store.create_reviewer_decision(
        state["query_job_id"], approved=False, reviewer=decision.get("reviewer", "unknown"),
        comments=decision.get("comments"),
    )
    store.update_job_status(state["query_job_id"], "evidence_rejected")
    store.write_audit_record(
        state["query_job_id"], decision.get("reviewer", "unknown"), "evidence_rejected",
        decision.get("comments") or "no comments",
    )
    print(f"[evidence_rejected] job {state['query_job_id']}")
    return {"final_answer": None, "status": "evidence_rejected"}


def evidence_escalated_node(state: LegalAgentState) -> dict:
    """Insufficient/uncertain evidence routed to senior review instead of a plain rejection."""
    store = get_store()
    decision = state["evidence_decision"]
    reviewer = decision.get("reviewer", "unknown")
    reason = decision.get("comments") or "escalated for senior review"

    store.create_reviewer_decision(state["query_job_id"], approved=False, reviewer=reviewer, comments=reason)
    store.update_job_status(state["query_job_id"], "evidence_escalated")
    store.write_audit_record(state["query_job_id"], reviewer, "evidence_escalated", reason)

    print(f"[evidence_escalated] job {state['query_job_id']}: {reason}")
    return {"final_answer": None, "status": "evidence_escalated"}


# writes the draft answer from verified evidence only
def synthesizer_node(state: LegalAgentState) -> dict:
    if state.get("route") == "whole_document":
        full_text = state["hybrid_hits"][0]["text"] if state["hybrid_hits"] else ""
        summary_text = generate_document_summary(full_text)
        result = {"answer": summary_text, "citations": [], "risk_level": None, "has_uncertainty": False}
    else:
        result = synthesize_legal_answer(
            state["question"], state["hybrid_hits"], state["graph_hits"], state["evidence_verdict"]
        )
    store = get_store()
    store.write_audit_record(
        state["query_job_id"], "synthesizer", "draft_answer_generated", result["answer"][:500]
    )
    print(f"[synthesizer] draft ready ({len(result['answer'])} chars, risk={result.get('risk_level')})")
    return {
        "draft_answer": result["answer"],
        "draft_citations": result.get("citations", []),
        "draft_risk_level": result.get("risk_level"),
        "draft_has_uncertainty": result.get("has_uncertainty", False),
    }


# second checkpoint: reviewer can approve, ask for revisions, or reject the draft
def human_answer_checkpoint_node(state: LegalAgentState) -> dict:
    """Pauses for the reviewer's approve/revise/reject decision on the draft answer."""
    payload = {
        "type": "answer_approval_request",
        "query_job_id": state["query_job_id"],
        "question": state["question"],
        "draft_answer": state["draft_answer"],
        "draft_citations": state.get("draft_citations", []),
        "draft_risk_level": state.get("draft_risk_level"),
        "draft_has_uncertainty": state.get("draft_has_uncertainty", False),
        "evidence_verdict": state["evidence_verdict"],
        "revision_round": state.get("answer_revision_count", 0),
        "max_answer_revisions": state.get("max_answer_revisions", DEFAULT_MAX_ANSWER_REVISIONS),
        "message": "Resume with {'action': 'approve'|'revise'|'reject'|'escalate', 'reviewer': str, "
                    "'comments': str|None, 'edited_answer': str|None}. "
                    "'comments' is REQUIRED when action is 'revise', it's what the LLM "
                    "reasons over to produce the next draft. 'edited_answer', if given with "
                    "'approve', overrides the draft text verbatim instead of using it as-is. "
                    "'escalate' routes this to senior review instead of finalizing or rejecting.",
    }
    decision = interrupt(payload)
    return {"answer_decision": decision}


def route_after_answer_checkpoint(state: LegalAgentState) -> str:
    decision = state["answer_decision"]
    action = decision.get("action")

    if action == "approve":
        return "finalize"

    if action == "revise":
        if state.get("answer_revision_count", 0) >= state.get("max_answer_revisions", DEFAULT_MAX_ANSWER_REVISIONS):
            # too many revision rounds, bail out instead of looping forever
            return "answer_rejected"
        return "revise_answer"

    if action == "escalate":
        return "answer_escalated"

    # "reject" or anything unrecognized falls through to rejected
    return "answer_rejected"


def answer_escalated_node(state: LegalAgentState) -> dict:
    """Routes the draft to senior review instead of finalizing or rejecting it outright.
    No graph write-back happens until a senior reviewer later approves or rejects it."""
    store = get_store()
    decision = state["answer_decision"]
    reviewer = decision.get("reviewer", "unknown")
    reason = decision.get("comments") or "escalated for senior review"

    store.create_reviewer_decision(state["query_job_id"], approved=False, reviewer=reviewer, comments=reason)
    store.update_job_status(state["query_job_id"], "escalated")
    store.write_audit_record(state["query_job_id"], reviewer, "answer_escalated", reason)

    print(f"[answer_escalated] job {state['query_job_id']}: {reason}")
    return {"final_answer": None, "status": "escalated"}


def revise_answer_node(state: LegalAgentState) -> dict:
    """Sends the reviewer's feedback to the LLM against the same evidence and gets a revised draft."""
    decision = state["answer_decision"]
    feedback = decision.get("comments") or ""
    revision_round = state.get("answer_revision_count", 0) + 1

    result = revise_legal_answer(
        question=state["question"],
        previous_answer=state["draft_answer"],
        hybrid_hits=state["hybrid_hits"],
        graph_hits=state["graph_hits"],
        evidence_verdict=state["evidence_verdict"],
        reviewer_feedback=feedback,
    )

    store = get_store()
    store.write_audit_record(
        state["query_job_id"], decision.get("reviewer", "unknown"), "answer_revision_requested",
        f"round={revision_round} feedback={feedback[:300]}",
    )
    print(f"[revise_answer] round {revision_round}: incorporated reviewer feedback")

    return {
        "draft_answer": result["answer"],
        "draft_citations": result.get("citations", []),
        "draft_risk_level": result.get("risk_level"),
        "draft_has_uncertainty": result.get("has_uncertainty", False),
        "answer_revision_count": revision_round,
    }


def answer_rejected_node(state: LegalAgentState) -> dict:
    """Terminal rejection of the answer: no final_answer, no graph write-back."""
    store = get_store()
    decision = state["answer_decision"]
    reviewer = decision.get("reviewer", "unknown")
    reason = decision.get("comments") or (
        "max_answer_revisions exceeded" if decision.get("action") == "revise" else "no comments"
    )

    store.create_reviewer_decision(state["query_job_id"], approved=False, reviewer=reviewer, comments=reason)
    store.update_job_status(state["query_job_id"], "rejected")
    store.write_audit_record(state["query_job_id"], reviewer, "answer_rejected", reason)

    print(f"[answer_rejected] job {state['query_job_id']}: {reason}")
    return {"final_answer": None, "status": "rejected"}


# finalize + optional graph update, only reached via the "approve" action
def finalize_node(state: LegalAgentState) -> dict:
    store = get_store()
    decision = state["answer_decision"]
    reviewer = decision.get("reviewer", "unknown")
    final_answer = decision.get("edited_answer") or state["draft_answer"]

    store.create_reviewer_decision(state["query_job_id"], approved=True, reviewer=reviewer,
                                    comments=decision.get("comments"))
    store.write_audit_record(state["query_job_id"], reviewer, "review_decision", "approved")
    store.store_query_answer(state["query_job_id"], final_answer)
    store.update_job_status(state["query_job_id"], "answered")

    print(f"[finalize] job {state['query_job_id']} -> answered "
          f"(after {state.get('answer_revision_count', 0)} revision round(s))")
    return {"final_answer": final_answer, "status": "answered"}


def route_after_finalize(state: LegalAgentState) -> str:
    """Graph update only applies to approved answers that came from the graph route."""
    if state["status"] == "answered" and state["route"] == "graph" and state["graph_hits"]:
        return "graph_update"
    return END


def graph_update_node(state: LegalAgentState) -> dict:
    """Records the reviewed Q&A as an AnsweredQuestion node citing the clauses it used."""
    store = get_store()
    cited_clause_ids = sorted({
        row["clause_id"] for row in state["graph_hits"] if isinstance(row, dict) and row.get("clause_id")
    })
    answered_id = store.record_answered_question(
        state["query_job_id"], state["question"], state["final_answer"], cited_clause_ids
    )
    store.write_audit_record(
        state["query_job_id"], "system", "graph_updated",
        f"answered_id={answered_id} cited_clauses={len(cited_clause_ids)}",
    )
    print(f"[graph_update] recorded AnsweredQuestion {answered_id} citing {len(cited_clause_ids)} clause(s)")
    return {}


# wires all the nodes together
def build_legal_agent_graph():
    graph = StateGraph(LegalAgentState)

    graph.add_node("start_job", start_job_node)
    graph.add_node("router", router_node)
    graph.add_node("hybrid_search_agent", hybrid_search_agent_node)
    graph.add_node("graph_rag_agent", graph_rag_agent_node)
    graph.add_node("whole_document_agent", whole_document_agent_node)
    graph.add_node("auditor", auditor_node)
    graph.add_node("human_evidence_checkpoint", human_evidence_checkpoint_node)
    graph.add_node("evidence_rejected", evidence_rejected_node)
    graph.add_node("evidence_escalated", evidence_escalated_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("human_answer_checkpoint", human_answer_checkpoint_node)
    graph.add_node("revise_answer", revise_answer_node)
    graph.add_node("answer_rejected", answer_rejected_node)
    graph.add_node("answer_escalated", answer_escalated_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("graph_update", graph_update_node)

    graph.set_entry_point("start_job")
    graph.add_edge("start_job", "router")

    # router picks exactly one of the three paths
    graph.add_conditional_edges("router", route_after_router, {
        "hybrid_search_agent": "hybrid_search_agent",
        "graph_rag_agent": "graph_rag_agent",
        "whole_document_agent": "whole_document_agent",
        "auditor": "auditor",  # "direct" path: skip retrieval entirely
    })
    graph.add_edge("hybrid_search_agent", "auditor")
    graph.add_edge("graph_rag_agent", "auditor")
    graph.add_edge("whole_document_agent", "auditor")

    # evidence checkpoint - has to pass before we generate anything
    graph.add_edge("auditor", "human_evidence_checkpoint")
    graph.add_conditional_edges("human_evidence_checkpoint", route_after_evidence_checkpoint, {
        "synthesizer": "synthesizer",
        "evidence_rejected": "evidence_rejected",
        "evidence_escalated": "evidence_escalated",
    })
    graph.add_edge("evidence_rejected", END)
    graph.add_edge("evidence_escalated", END)

    # answer checkpoint - "revise" loops back until approve/reject or the revision cap kicks in
    graph.add_edge("synthesizer", "human_answer_checkpoint")
    graph.add_conditional_edges("human_answer_checkpoint", route_after_answer_checkpoint, {
        "finalize": "finalize",
        "revise_answer": "revise_answer",
        "answer_rejected": "answer_rejected",
        "answer_escalated": "answer_escalated",
    })
    graph.add_edge("revise_answer", "human_answer_checkpoint")
    graph.add_edge("answer_rejected", END)
    graph.add_edge("answer_escalated", END)

    # optional graph write-back, only for approved graph-path answers
    graph.add_conditional_edges("finalize", route_after_finalize, {
        "graph_update": "graph_update",
        END: END,
    })
    graph.add_edge("graph_update", END)

    # checkpointer is a shared singleton so resume() can find where invoke() paused
    return graph.compile(checkpointer=get_checkpointer())


# quick demo: one revise round then an approve

if __name__ == "__main__":
    app = build_legal_agent_graph()
    config = {"configurable": {"thread_id": "legal-agent-demo-1"}}

    result = app.invoke(
        {
            "question": "Show all contracts where ABC Ltd. is the vendor, the same clause "
                        "appears in another contract, and that clause has been interpreted "
                        "by multiple judgments.",
            "collection_name": "abc_ltd_msa_pdf",
        },
        config=config,
    )

    print("\n--- PAUSED: EVIDENCE CHECKPOINT ---")
    print(result["__interrupt__"])

    result = app.invoke(
        Command(resume={"proceed": True, "reviewer": "jane.doe", "comments": "Evidence looks solid."}),
        config=config,
    )

    print("\n--- PAUSED: ANSWER CHECKPOINT (round 1) ---")
    print(result["__interrupt__"])

    # Reviewer asks for a change instead of approving outright.
    result = app.invoke(
        Command(resume={
            "action": "revise",
            "reviewer": "jane.doe",
            "comments": "Missing Section 4.2 termination clauses, please check whether "
                        "the termination notice period is also part of the conflict.",
        }),
        config=config,
    )

    print("\n--- PAUSED: ANSWER CHECKPOINT (round 2, after revision) ---")
    print(result["__interrupt__"])

    # Reviewer approves the revised answer.
    result = app.invoke(
        Command(resume={"action": "approve", "reviewer": "jane.doe"}),
        config=config,
    )

    print("\n--- FINAL ANSWER ---")
    print(result["final_answer"])
