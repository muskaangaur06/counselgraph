"""Contract-level metadata extraction via a single LLM pass.

Populates the fields HybridSearchAgent's metadata_filter can filter on
(contract_type, parties, dates, monetary value, governing law), plus a
human-readable summary shown at the ingestion approval checkpoint.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from ..llm_client import call_json, call_text

_METADATA_SYSTEM_PROMPT = """You are a contract analyst. Given the text of a \
contract (or the first portion of it), extract high-level metadata about it.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "contract_type": string|null,        // one of: Affiliate Agreement, Development, Distributor,
                                        // Endorsement, Franchise, Hosting, IP, Joint Venture,
                                        // License Agreement, Maintenance, Manufacturing, Marketing,
                                        // Non-Compete/Solicit, Outsourcing, Promotion, Reseller,
                                        // Service, Sponsorship, Strategic Alliance, Supply,
                                        // Transportation, Lease, or null if none fit
  "parties": [string],                 // flat list of party/company names (kept for Chroma filtering)
  "parties_with_roles": [               // same parties, but with their role in THIS contract
    {"name": string, "role": string}   // e.g. {"name": "Podium Properties, LLC", "role": "Lessor"}
  ],
  "subject_matter": string|null,       // one sentence: what the contract covers, e.g. the premises
                                        // being leased and its address, the product/service licensed, etc.
  "effective_date": string|null,       // ISO 8601 YYYY-MM-DD, or null if not stated
  "end_date": string|null,             // ISO 8601 YYYY-MM-DD, or null if not stated / perpetual
  "monetary_value": number|null,       // total contract value if stated, in the currency's numeric amount
  "governing_law_country": string|null // two-letter ISO country code, or null if not stated
}
If a field cannot be determined from the text, use null (or [] for parties/parties_with_roles)."""


def extract_contract_metadata(document_text: str, max_chars: int = 6000) -> dict:
    """document_text should be the first few chunks concatenated: the fields we want are almost always stated early."""
    result = call_json(_METADATA_SYSTEM_PROMPT, document_text[:max_chars])
    return result if isinstance(result, dict) else {}


def to_epoch(date_str: Optional[str]) -> Optional[float]:
    """Convert an ISO 8601 date to a Unix epoch so Chroma can filter it with $gte/$lte."""
    if not date_str:
        return None
    try:
        return datetime.combine(date.fromisoformat(date_str), datetime.min.time()).timestamp()
    except ValueError:
        return None


def build_chunk_metadata(contract_metadata: dict) -> dict:
    """Flatten extract_contract_metadata()'s output into scalar Chroma metadata (parties joined into one string)."""
    parties = contract_metadata.get("parties") or []
    return {
        "contract_type": contract_metadata.get("contract_type") or "",
        "parties": ", ".join(parties),
        "effective_date_epoch": to_epoch(contract_metadata.get("effective_date")) or 0,
        "end_date_epoch": to_epoch(contract_metadata.get("end_date")) or 0,
        "monetary_value": float(contract_metadata.get("monetary_value") or 0),
        "governing_law_country": (contract_metadata.get("governing_law_country") or "").upper(),
    }


_SUMMARY_SYSTEM_PROMPT = """You are a legal analyst preparing a review-ready summary of a \
contract for a legal team. You are given the full text of the document (reconstructed from \
its stored chunks, in order). Produce a summary covering, where present in the text:

- Parties and their roles
- Subject matter / purpose of the agreement
- Key obligations of each party
- Payment terms
- Term, renewal, and termination provisions
- Liability and indemnification terms
- Confidentiality obligations
- Governing law and dispute resolution
- Any other clause that would be material to a reviewer (e.g. audit rights, data protection)

Write in plain prose organized under short headings. If a section (e.g. payment terms) is not \
present in the text, say so briefly rather than omitting it silently. Do not invent details not \
present in the text. This is a draft for human review, not a final legal opinion."""


def generate_document_summary(full_text: str, max_chars: int = 24000) -> str:
    """Generates a whole-document summary from the full reconstructed chunk text
    (not from similarity search, since a generic "summarize this" question has
    nothing for retrieval to rank against). max_chars caps the input to a
    reasonable prompt size for long documents."""
    truncated = full_text[:max_chars]
    note = "" if len(full_text) <= max_chars else "\n\n[Note: document truncated for length.]"
    return call_text(_SUMMARY_SYSTEM_PROMPT, truncated + note, max_tokens=2000)


def format_executive_summary(document_name: str, job_id: str, contract_metadata: dict) -> dict:
    """Build the executive-summary block shown at the top of the ingestion approval payload."""
    return {
        "document_name": document_name,
        "job_id": job_id,
        "contract_type": contract_metadata.get("contract_type"),
        "subject_matter": contract_metadata.get("subject_matter"),
        "parties": contract_metadata.get("parties_with_roles") or [
            {"name": name, "role": None} for name in (contract_metadata.get("parties") or [])
        ],
        "effective_date": contract_metadata.get("effective_date"),
        "end_date": contract_metadata.get("end_date"),
        "governing_law_country": contract_metadata.get("governing_law_country"),
    }
