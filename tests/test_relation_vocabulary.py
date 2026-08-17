"""The controlled vocabulary for relationship predicates (extract/schema.py).

`relation` used to be free text, so synonyms became distinct edges between the same pair of nodes
(Database.upsert_relationship keys on the relation). These lock in that synonyms converge and that
an unrecognized predicate degrades instead of failing the whole article's extraction.
"""

import pytest

from pyro.db.keys import relationship_key
from pyro.extract.schema import (
    RELATION_KINDS,
    ExtractedGraph,
    ExtractedRelationship,
    merge_graph_chunks,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("writes_to", "writes_to"),
        ("writes to", "writes_to"),
        ("Writes To", "writes_to"),
        ("writes-to", "writes_to"),
        ("persists to", "writes_to"),
        ("stores data in", "writes_to"),
        ("publishes to", "publishes_to"),
        ("reads from", "reads_from"),
        ("invokes", "calls"),
        ("runs on", "deployed_on"),
    ],
)
def test_synonyms_converge_on_one_predicate(raw, expected):
    rel = ExtractedRelationship(source="A", target="B", relation=raw)
    assert rel.relation == expected


def test_original_wording_is_preserved_when_it_was_rewritten():
    rel = ExtractedRelationship(source="A", target="B", relation="persists to")
    assert rel.relation == "writes_to"
    assert rel.relation_phrase == "persists to"


def test_canonical_input_keeps_no_redundant_phrase():
    rel = ExtractedRelationship(source="A", target="B", relation="writes_to")
    assert rel.relation_phrase is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The dominant real-world shape: a known predicate plus a trailing qualifier. Matching
        # only whole strings sent all of these to the fallback.
        ("uses for distributed tracing", "depends_on"),
        ("downloads rule sets from", "reads_from"),
        ("proxies calls to", "routes_to"),
        ("pushes schema to", "publishes_to"),
        ("captures metrics from", "monitors"),
        ("orchestrates experiments on", "deploys"),
        ("hosts migrated route handlers from", "owns"),
    ],
)
def test_trailing_qualifiers_are_dropped(raw, expected):
    assert ExtractedRelationship(source="A", target="B", relation=raw).relation == expected


def test_longer_synonym_wins_over_a_shorter_one_it_starts_with():
    """"routes requests to" must not be read as the bare "routes" if a more specific entry
    exists — synonyms are tried longest-first for exactly this."""
    assert (
        ExtractedRelationship(source="A", target="B", relation="routes requests to").relation
        == "routes_to"
    )


def test_prefix_match_respects_word_boundaries():
    """"user" starts with "use" but is not the verb — a bare prefix test would mis-fire."""
    rel = ExtractedRelationship(source="A", target="B", relation="userland thing")
    assert rel.relation == "depends_on"
    assert rel.relation_phrase == "userland thing"


def test_unknown_predicate_falls_back_instead_of_failing_validation():
    """A single unrecognized predicate must not reject the article and burn a cascade tier."""
    rel = ExtractedRelationship(source="A", target="B", relation="frobnicates via")
    assert rel.relation == "depends_on"
    assert rel.relation_phrase == "frobnicates via"


def test_every_vocabulary_value_validates():
    for kind in RELATION_KINDS:
        assert ExtractedRelationship(source="A", target="B", relation=kind).relation == kind


def test_synonym_edges_collapse_into_one_when_merging_chunks():
    chunks = [
        ExtractedGraph(
            relationships=[{"source": "API", "target": "DB", "relation": "writes to"}]
        ),
        ExtractedGraph(
            relationships=[{"source": "API", "target": "DB", "relation": "persists to"}]
        ),
        ExtractedGraph(
            relationships=[{"source": "API", "target": "DB", "relation": "stores data in"}]
        ),
    ]
    merged = merge_graph_chunks(chunks)
    assert len(merged.relationships) == 1
    assert merged.relationships[0].relation == "writes_to"


def test_synonyms_produce_the_same_storage_key():
    """The end of the bug: these used to be three separate edges drawn on the same diagram."""
    keys = {
        relationship_key(
            "Acme",
            "API",
            ExtractedRelationship(source="API", target="DB", relation=phrase).relation,
            "DB",
        )
        for phrase in ("writes to", "writes-to", "persists to", "stores data in")
    }
    assert len(keys) == 1
