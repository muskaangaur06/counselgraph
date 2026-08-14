"""Phase 13: counterparty derivation from extracted parties.

The upload form's Counterparty field is optional, but the ingestion pipeline
already extracts the contract's parties -- leaving the document header's
Counterparty badge blank in that case hides information the system genuinely
knows. These cover _derive_counterparty's preference order and its refusal to
guess when no role disambiguates the sides.
"""

from counsel_graph.graphrag.langgraph_agent import _derive_counterparty


def test_explicit_vendor_name_wins_over_extracted_parties():
    result = _derive_counterparty(
        [{"name": "Acme Corp", "role": "Vendor"}],
        vendor_name="Typed By Reviewer Ltd",
    )
    assert result == "Typed By Reviewer Ltd"


def test_party_with_supplying_role_is_chosen():
    parties = [
        {"name": "Tata Steel Limited", "role": "Customer"},
        {"name": "Global Supply Co", "role": "Supplier"},
    ]
    assert _derive_counterparty(parties) == "Global Supply Co"


def test_role_matching_is_case_insensitive_and_substring_tolerant():
    parties = [
        {"name": "TCS", "role": "Client"},
        {"name": "Nimbus Hosting LLC", "role": "SERVICE PROVIDER (the Provider)"},
    ]
    assert _derive_counterparty(parties) == "Nimbus Hosting LLC"


def test_single_party_is_used_even_without_a_role():
    assert _derive_counterparty([{"name": "Solo Party Inc", "role": None}]) == "Solo Party Inc"


def test_ambiguous_parties_are_joined_rather_than_guessed():
    """No role identifies the supplying side, so listing both is honest;
    silently picking one would put a possibly-wrong name on the header."""
    parties = [
        {"name": "Alpha Holdings", "role": "Party A"},
        {"name": "Beta Ventures", "role": "Party B"},
    ]
    assert _derive_counterparty(parties) == "Alpha Holdings / Beta Ventures"


def test_plain_string_parties_are_supported():
    assert _derive_counterparty(["Just A Name Ltd"]) == "Just A Name Ltd"


def test_no_parties_yields_none():
    assert _derive_counterparty([]) is None
    assert _derive_counterparty(None) is None


def test_blank_names_are_ignored():
    assert _derive_counterparty([{"name": "   ", "role": "Vendor"}]) is None


def test_result_is_truncated_to_column_width():
    """Document.counterparty is String(200); a long joined list must not
    overflow the column and fail the whole metadata update."""
    parties = [{"name": "X" * 150, "role": "A"}, {"name": "Y" * 150, "role": "B"}]
    assert len(_derive_counterparty(parties)) <= 200
