"""Section 5: Negotiation Playbook Generator.

For each medium/high risk-flagged clause, builds a playbook entry: current
language, the already-computed risk rationale, fallback positions ranked
ideal-to-acceptable (sourced from the org profile's required_clause_checklist/
approved language variants when available, else LLM-generated, clearly labeled
which source it came from), and a suggested redline.
"""

from __future__ import annotations

import json
from typing import Optional

from ..llm_client import call_json
from ..guardrails import wrap_untrusted

_FALLBACK_SYSTEM_PROMPT = """You are a contract negotiation strategist. Given a risky \
contract clause and the reason it was flagged, propose a negotiation playbook.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "fallback_positions": [string, string, ...],  // 2-4 positions, ranked from the ideal
                                                  // ask down to the minimum acceptable position
  "suggested_redline": string                    // concrete replacement language for the clause
}"""


def _llm_generate_fallback(clause_text: str, risk_rationale: str) -> dict:
    payload = json.dumps({"clause_text": clause_text, "risk_rationale": risk_rationale})
    result = call_json(_FALLBACK_SYSTEM_PROMPT, wrap_untrusted("clause_and_rationale", payload))
    if not isinstance(result, dict):
        return {"fallback_positions": [], "suggested_redline": None}
    return result


def build_playbook_entry(clause_text: str, clause_type: Optional[str], risk_rationale: str,
                          org_profile_id: Optional[str] = None, standards_context: Optional[dict] = None) -> dict:
    """Returns {"current_language", "fallback_positions", "fallback_source", "suggested_redline"}.
    fallback_source is "org_profile" when the org profile has pre-defined approved
    language for this clause_type (used as the single ideal fallback position plus
    the current language as the acceptable floor), else "llm_generated".

    standards_context (optional, Phase 5): see deviation.compute_deviation -- when
    given, the approved language is resolved through the 8-level hierarchy.
    """
    approved_text = None
    if clause_type:
        try:
            from ..db.repository import get_approved_clause_language
            approved_text = get_approved_clause_language(clause_type, org_profile_id, standards_context=standards_context)
        except Exception:
            approved_text = None

    if approved_text:
        return {
            "current_language": clause_text,
            "fallback_positions": [approved_text, clause_text],
            "fallback_source": "org_profile",
            "suggested_redline": approved_text,
        }

    generated = _llm_generate_fallback(clause_text, risk_rationale)
    return {
        "current_language": clause_text,
        "fallback_positions": generated.get("fallback_positions") or [],
        "fallback_source": "llm_generated",
        "suggested_redline": generated.get("suggested_redline"),
    }
