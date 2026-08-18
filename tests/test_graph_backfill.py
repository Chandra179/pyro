"""The one-off backfill (graph/backfill.py) that rewrites pre-vocabulary edges.

The two properties that matter: synonym edges collapse into one rather than being counted twice,
and the command is re-runnable — an edge an earlier run folded into the fallback must be
reclassified once the synonym table learns its phrasing, not skipped as already-canonical.
"""

from pyro.db.keys import relationship_key
from pyro.graph.backfill import canonicalize_relations


class _FakeDb:
    """Enough of Database's relationship surface for the backfill."""

    def __init__(self, company, edges):
        self.company = company
        self.edges = {e["_key"]: e for e in edges}
        self.relationships = self

    def list_relationships(self, company_name):
        return list(self.edges.values())

    def upsert_relationship(
        self,
        company_name,
        source,
        target,
        relation,
        as_of,
        source_article_id,
        relation_phrase=None,
        extra_source_article_ids=None,
    ):
        key = relationship_key(company_name, source, relation, target)
        ids = list(dict.fromkeys(extra_source_article_ids or []))
        if source_article_id and source_article_id not in ids:
            ids.append(source_article_id)
        self.edges[key] = {
            "_key": key,
            "source": source,
            "target": target,
            "relation": relation,
            "relation_phrase": relation_phrase,
            "as_of": as_of,
            "source_article_id": source_article_id,
            "source_article_ids": ids,
        }

    def delete_key(self, key):
        self.edges.pop(key, None)


def _edge(company, source, relation, target, phrase=None):
    return {
        "_key": relationship_key(company, source, relation, target),
        "source": source,
        "target": target,
        "relation": relation,
        "relation_phrase": phrase,
        "as_of": None,
        "source_article_id": "a1",
    }


def test_synonym_edges_collapse_into_one():
    db = _FakeDb(
        "acme",
        [
            _edge("acme", "API", "writes to", "DB"),
            _edge("acme", "API", "persists to", "DB"),
            _edge("acme", "API", "stores data in", "DB"),
        ],
    )
    result = canonicalize_relations(db, "acme")

    assert result["examined"] == 3
    assert result["collapsed"] == 2
    remaining = db.list_relationships("acme")
    assert len(remaining) == 1
    assert remaining[0]["relation"] == "writes_to"


def test_original_wording_is_kept_as_an_audit_trail():
    db = _FakeDb("acme", [_edge("acme", "App", "sends requests to", "Gateway")])
    canonicalize_relations(db, "acme")
    edge = db.list_relationships("acme")[0]
    assert edge["relation"] == "calls"
    assert edge["relation_phrase"] == "sends requests to"


def test_already_canonical_edges_are_left_alone():
    db = _FakeDb("acme", [_edge("acme", "API", "writes_to", "DB")])
    result = canonicalize_relations(db, "acme")
    assert result["rewritten"] == 0
    assert db.list_relationships("acme")[0]["relation"] == "writes_to"


def test_rerun_reclassifies_from_the_preserved_phrase(monkeypatch):
    """An edge an earlier run folded into the fallback must be reclassified once the synonym table
    learns its phrasing — which only works because normalization reads relation_phrase, not the
    already-canonical stored value."""
    from pyro.extract import schema

    db = _FakeDb("acme", [_edge("acme", "A", "depends_on", "B", phrase="frobnicates via")])

    # First pass: unknown phrasing, so it stays on the fallback.
    assert canonicalize_relations(db, "acme")["rewritten"] == 0

    monkeypatch.setitem(schema._RELATION_SYNONYMS, "frobnicates via", "calls")
    monkeypatch.setattr(
        schema,
        "_SYNONYMS_BY_LENGTH",
        sorted(schema._RELATION_SYNONYMS.items(), key=lambda kv: -len(kv[0])),
    )

    assert canonicalize_relations(db, "acme")["rewritten"] == 1
    assert db.list_relationships("acme")[0]["relation"] == "calls"
