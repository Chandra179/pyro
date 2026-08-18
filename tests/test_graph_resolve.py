"""The deterministic name-resolution pre-pass (graph/resolve.py) that runs before the merge LLM.

The behavior worth protecting is the balance: absorb trivial spelling drift without a model call,
but never silently fuse two systems that merely look alike — a false merge is far harder to notice
than a missed one, and the LLM tier still catches misses.
"""

from pyro.graph.resolve import (
    ResolvedName,
    _is_generic,
    candidate_names,
    resolve_known_names,
)


def test_exact_match_resolves_without_the_llm():
    mapping, unresolved = resolve_known_names(["Kafka"], ["Kafka", "Cassandra"])
    assert mapping == {"Kafka": ResolvedName(canonical="Kafka", method="exact")}
    assert unresolved == []


def test_case_and_punctuation_drift_resolves_to_the_canonical_spelling():
    mapping, unresolved = resolve_known_names(
        ["kafka", "USER-SERVICE"], ["Kafka", "User Service"]
    )
    assert mapping == {
        "kafka": ResolvedName(canonical="Kafka", method="exact"),
        "USER-SERVICE": ResolvedName(canonical="User Service", method="exact"),
    }
    assert unresolved == []


def test_near_miss_resolves_by_fuzzy_match():
    mapping, unresolved = resolve_known_names(["Users Service"], ["User Service"])
    assert mapping["Users Service"].canonical == "User Service"
    assert mapping["Users Service"].method.startswith("fuzzy:")
    assert unresolved == []


def test_a_longer_name_is_not_folded_into_a_shorter_one_it_contains():
    """"Kafka Connect" is a different system from "Kafka". This is exactly the collapse a
    substring-friendly scorer (WRatio) would have made, and why resolve.py uses token_sort_ratio."""
    mapping, unresolved = resolve_known_names(["Kafka Connect"], ["Kafka"])
    assert mapping == {}
    assert unresolved == ["Kafka Connect"]


def test_short_names_must_match_exactly():
    """S3 and S4 are one character apart and are not the same datastore."""
    mapping, unresolved = resolve_known_names(["S4"], ["S3"])
    assert mapping == {}
    assert unresolved == ["S4"]


def test_genuinely_new_names_are_left_for_the_model():
    mapping, unresolved = resolve_known_names(["Titus"], ["Kafka", "Cassandra"])
    assert mapping == {}
    assert unresolved == ["Titus"]


def test_empty_graph_leaves_everything_unresolved():
    mapping, unresolved = resolve_known_names(["Kafka", "Titus"], [])
    assert mapping == {}
    assert unresolved == ["Kafka", "Titus"]


def test_generic_name_skips_exact_match_and_goes_to_the_model():
    """"new microservice" is a relative description, not a stable identifier — two unrelated
    articles can independently produce the exact same phrase for unrelated systems, so an exact
    string match here must not be trusted the way "Kafka" == "Kafka" is."""
    mapping, unresolved = resolve_known_names(
        ["new microservice"], ["new microservice", "Kafka"]
    )
    assert mapping == {}
    assert unresolved == ["new microservice"]


def test_generic_name_skips_fuzzy_match_too():
    mapping, unresolved = resolve_known_names(
        ["old API service"], ["old API services", "Kafka"]
    )
    assert mapping == {}
    assert unresolved == ["old API service"]


def test_real_proper_nouns_starting_with_a_qualifier_word_are_not_treated_as_generic():
    """"New Relic" starts with "new" but is a specific product name, not a relative description —
    the generic check requires a trailing generic noun (service/system/gateway/...) too, precisely
    so this stays on the fast exact-match path."""
    mapping, unresolved = resolve_known_names(["New Relic"], ["New Relic"])
    assert mapping == {"New Relic": ResolvedName(canonical="New Relic", method="exact")}
    assert unresolved == []


def test_is_generic_examples():
    assert _is_generic("new microservice")
    assert _is_generic("the old service")
    assert _is_generic("legacy Kafka cluster")
    assert not _is_generic("New Relic")
    assert not _is_generic("Cassandra")
    assert not _is_generic("Open Connect")


def test_candidate_names_returns_everything_below_the_cap():
    existing = ["Kafka", "Cassandra", "Titus"]
    assert candidate_names(["Zuul"], existing, limit=40) == sorted(existing)


def test_candidate_names_caps_a_large_graph():
    existing = [f"service-{i}" for i in range(200)] + ["payments gateway"]
    selected = candidate_names(["payment gateway"], existing, limit=10)
    assert len(selected) <= 10
    # The relevant name survives the cut — that's the point of retrieving rather than truncating.
    assert "payments gateway" in selected


def test_candidate_names_limit_none_returns_everything():
    existing = [f"service-{i}" for i in range(200)]
    assert len(candidate_names(["x"], existing, limit=None)) == 200
