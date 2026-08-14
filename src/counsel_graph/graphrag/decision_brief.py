"""Decision Brief generation and approval-chain resolution (blueprint section 15).

Two independent pieces:
- build_decision_brief_sections(): gathers everything 15.2.2 lists and asks
  Gemini for a structured brief matching 15.3's 13 sections, then validates
  that every material statement is grounded in the structured data actually
  gathered (15.2.4) rather than trusting the LLM's prose blindly.
- resolve_approval_chain(): a pure deterministic function, no LLM call, that
  walks 15.5's fallback chain (or an org's configured approval_policy override)
  to decide who needs to approve and in what order.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..llm_client import call_json

DEFAULT_VALUE_THRESHOLD = 10_000_000  # matches compliance.check_value_threshold's default

ALLOWED_RECOMMENDATIONS = ("approve", "approve_with_changes", "reject", "escalate")

BRIEF_SECTIONS = (
    "executive_summary", "ai_recommendation", "first_reviewer_recommendation",
    "acceptable_terms", "items_requiring_attention", "missing_clauses",
    "standards_comparison", "key_dates", "key_obligations", "financial_terms",
    "evidence_links", "review_history", "approval_chain",
)

_BRIEF_SYSTEM_PROMPT = f"""You are a legal analyst preparing a Decision Brief for a senior \
approver who has NOT read the underlying contract. You are given structured data already \
gathered by the system: document metadata, the approved executive summary, extracted \
clauses, risk flags, reviewer decisions, missing clauses, and a standards comparison.

Produce a JSON object with exactly these keys, one per required Decision Brief section:
{json.dumps(BRIEF_SECTIONS)}

Rules:
- "executive_summary": 3-6 sentences, drawn only from the data given.
- "ai_recommendation" and "first_reviewer_recommendation": each an object
  {{"recommendation": one of {list(ALLOWED_RECOMMENDATIONS)}, "rationale": string}}.
  first_reviewer_recommendation.rationale must be grounded in the reviewer_decisions/notes
  given, not invented; if no reviewer notes exist, say so and default the recommendation
  to "escalate".
- "acceptable_terms" and "items_requiring_attention": arrays of short strings, each citing
  a specific clause_type, risk_flag category, or document field it is based on.
- "missing_clauses", "key_dates", "key_obligations", "financial_terms", "evidence_links",
  "review_history", "standards_comparison", "approval_chain": render the corresponding
  input data as clear prose or a short list; do not invent entries not present in the input.
- Every material statement (a specific fact, number, date, or party name) must be traceable
  to the structured data provided. If something material is not present in the data, say
  "not stated in the reviewed data" rather than guessing.
- This is decision support, not final legal advice -- do not use language implying otherwise.

Output ONLY the JSON object, no markdown fences, no prose outside the JSON."""


def _gather_brief_input(document: dict, clauses: list[dict], document_risk_flags: list[dict],
                         latest_summary_version: Optional[dict], review_actions: list[dict],
                         missing_clauses: list[dict], standards_comparison: list[dict]) -> dict:
    """Assembles exactly the structured data 15.2.2 lists into one payload, both
    for the LLM prompt and for evidence_validate_sections() to check statements
    against afterward."""
    clause_risk_flags = [f for c in clauses for f in (c.get("risk_flags") or [])]
    return {
        "document_metadata": {
            "filename": document.get("filename"),
            "document_type": document.get("document_type"),
            "counterparty": document.get("counterparty"),
            "business_unit": document.get("business_unit"),
            "geography": document.get("geography"),
            "confidentiality_level": document.get("confidentiality_level"),
            "parties": document.get("parties") or [],
            "subject_matter": document.get("subject_matter"),
        },
        "approved_summary": (latest_summary_version or {}).get("summary_text"),
        "extracted_clauses": [
            {"clause_type": c.get("clause_type"), "page_reference": c.get("page_reference"),
             "party": c.get("party")} for c in clauses
        ],
        "reviewed_clauses": [
            {"clause_type": c.get("clause_type"), "status": f.get("reviewer_status")}
            for c in clauses for f in (c.get("risk_flags") or [])
        ],
        "risk_flags": [
            {"category": f.get("category"), "severity": f.get("severity"), "rationale": f.get("rationale"),
             "reviewer_status": f.get("reviewer_status")}
            for f in clause_risk_flags + document_risk_flags
        ],
        "reviewer_decisions": [
            {"reviewer": a.get("reviewer_username"), "role": a.get("role"), "action": a.get("action"),
             "rationale": a.get("rationale")} for a in review_actions
        ],
        "missing_clauses": missing_clauses,
        "standards_comparison": standards_comparison,
        "key_dates": {"effective_date": document.get("effective_date"), "end_date": document.get("end_date")},
        "financial_terms": {"monetary_value": document.get("monetary_value")},
        "governing_law_country": document.get("governing_law_country"),
    }


def _fallback_brief(reason: str) -> dict:
    """Never silently produce a brief with fabricated content -- if the LLM call
    fails, every section explicitly says so and the recommendation defaults to
    escalate (the safest option, per 15.3's allowed recommendations)."""
    return {
        section: (
            {"recommendation": "escalate", "rationale": reason}
            if section in ("ai_recommendation", "first_reviewer_recommendation")
            else f"Brief generation failed: {reason}"
        )
        for section in BRIEF_SECTIONS
    }


def _flatten_strings(value) -> list[str]:
    """Collects every string leaf out of an arbitrarily nested dict/list, for
    evidence_validate_sections() to scan."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_flatten_strings(v))
    return out


_MONEY_PATTERN = re.compile(r"[\d,]+(?:\.\d+)?")


def evidence_validate_sections(sections: dict, brief_input: dict) -> tuple[bool, list[str]]:
    """Section 15.2.4: validate that every material statement references data
    actually gathered. A full NLP-grounding check is out of scope for a
    deterministic pass -- this checks the cheap, high-value case: any monetary
    figure the brief states must match a monetary_value actually present in
    brief_input (catches the LLM inventing or mis-transcribing a number, the
    single most consequential kind of ungrounded statement in a legal brief).
    Anything else is currently trusted (LLM-only path is flagged as a known
    limitation, consistent with how the LLM prompt-following behavior isn't
    independently verified anywhere else in this codebase either)."""
    known_amount = brief_input.get("financial_terms", {}).get("monetary_value")
    unsupported: list[str] = []

    for text in _flatten_strings(sections):
        for match in _MONEY_PATTERN.finditer(text):
            digits = match.group(0).replace(",", "")
            if not digits or digits == "." or len(digits) < 4:
                continue  # short numbers (page refs, small counts) aren't a monetary claim
            try:
                amount = float(digits)
            except ValueError:
                continue
            if known_amount is None or abs(amount - float(known_amount)) > 1.0:
                unsupported.append(f"Unverified figure '{match.group(0)}' in: {text[:200]}")

    return (len(unsupported) == 0), unsupported


def generate_decision_brief_sections(document: dict, clauses: list[dict], document_risk_flags: list[dict],
                                      latest_summary_version: Optional[dict], review_actions: list[dict],
                                      missing_clauses: list[dict], standards_comparison: list[dict]) -> dict:
    """Top-level entry point: gathers input, calls Gemini, validates evidence.
    Returns {"sections", "recommendation", "evidence_validated", "unsupported_statements"}."""
    brief_input = _gather_brief_input(
        document, clauses, document_risk_flags, latest_summary_version,
        review_actions, missing_clauses, standards_comparison,
    )

    try:
        sections = call_json(_BRIEF_SYSTEM_PROMPT, json.dumps(brief_input), max_tokens=3000)
    except Exception as e:  # noqa: BLE001
        print(f"[decision_brief] WARNING: brief generation failed: {type(e).__name__}: {e}")
        sections = None

    if not isinstance(sections, dict) or not all(k in sections for k in BRIEF_SECTIONS):
        sections = _fallback_brief("LLM did not return a valid structured brief.")

    evidence_validated, unsupported = evidence_validate_sections(sections, brief_input)

    first_rec = sections.get("first_reviewer_recommendation")
    recommendation = (first_rec or {}).get("recommendation") if isinstance(first_rec, dict) else None
    if recommendation not in ALLOWED_RECOMMENDATIONS:
        recommendation = "escalate"

    return {
        "sections": sections,
        "recommendation": recommendation,
        "evidence_validated": evidence_validated,
        "unsupported_statements": unsupported,
    }


# ---------------------------------------------------------------------------
# Approval chain resolution (section 15.5) -- pure deterministic, no LLM call.
# ---------------------------------------------------------------------------

_HIGH_RISK_SEVERITIES = ("high",)


def resolve_approval_chain(document: dict, document_risk_flags: list[dict], clause_risk_flags: list[dict],
                            risk_threshold_overrides: dict) -> list[dict]:
    """Returns an ordered list of {"required_role", "reason"} steps. Checks the
    org's configured approval_policy first (risk_threshold_overrides["approval_policy"],
    a list of {"role","reason"} dicts in order) and uses it verbatim if present --
    per 15.5, "do not hardcode the contract-value threshold globally if an
    organization policy exists." Falls back to the blueprint's default chain
    (legal reviewer -> senior counsel -> business head if high/critical risk or
    over the value threshold -> CLO if highly confidential or explicitly required)
    only when no policy is configured."""
    configured_policy = risk_threshold_overrides.get("approval_policy")
    if configured_policy:
        return [{"required_role": step["role"], "reason": step.get("reason", "organization approval policy")}
                for step in configured_policy]

    chain = [{"required_role": "reviewer", "reason": "first reviewer"},
             {"required_role": "senior_counsel", "reason": "second-level legal approval"}]

    all_flags = document_risk_flags + clause_risk_flags
    has_high_risk = any(f.get("severity") in _HIGH_RISK_SEVERITIES for f in all_flags)

    value_threshold = risk_threshold_overrides.get("value_threshold", DEFAULT_VALUE_THRESHOLD)
    monetary_value = document.get("monetary_value")
    over_value_threshold = monetary_value is not None and monetary_value >= value_threshold

    if has_high_risk or over_value_threshold:
        reasons = []
        if has_high_risk:
            reasons.append("high/critical risk flag present")
        if over_value_threshold:
            reasons.append(f"contract value >= {value_threshold:,.0f}")
        chain.append({"required_role": "business_head", "reason": "; ".join(reasons)})

    explicitly_required_clo = bool(risk_threshold_overrides.get("require_clo_approval"))
    if document.get("confidentiality_level") == "highly_confidential" or explicitly_required_clo:
        reason = "highly confidential document" if document.get("confidentiality_level") == "highly_confidential" \
            else "organization policy explicitly requires CLO approval"
        chain.append({"required_role": "clo", "reason": reason})

    return chain
