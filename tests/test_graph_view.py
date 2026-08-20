"""build_graph_elements (api/graph_view.py): the dashboard's Graph view is meant to show the
*current* system map, so invalidated edges (a system's behavior after a later article says it was
replaced — see graph/merge.py's replaced_by handling) must not be drawn as if still live."""

from api.graph_view import build_graph_elements


def _entity(name, kind="service"):
    return {"name": name, "kind": kind, "domain": "Other"}


def test_valid_edge_is_drawn():
    entities = [_entity("A"), _entity("B")]
    relationships = [{"source": "A", "target": "B", "relation": "calls", "invalid_at": None}]
    elements = build_graph_elements(entities, relationships)
    assert len(elements["edges"]) == 1


def test_invalidated_edge_is_not_drawn():
    entities = [_entity("A"), _entity("B")]
    relationships = [
        {"source": "A", "target": "B", "relation": "calls", "invalid_at": "2026-01-01T00:00:00+00:00"}
    ]
    elements = build_graph_elements(entities, relationships)
    assert elements["edges"] == []


def test_edge_without_invalid_at_field_is_still_drawn():
    """Edges written before this field existed have no `invalid_at` key at all — .get() must
    treat that the same as "not invalidated", not crash or hide them."""
    entities = [_entity("A"), _entity("B")]
    relationships = [{"source": "A", "target": "B", "relation": "calls"}]
    elements = build_graph_elements(entities, relationships)
    assert len(elements["edges"]) == 1


def test_mix_of_valid_and_invalidated_edges():
    entities = [_entity("A"), _entity("B"), _entity("C")]
    relationships = [
        {"source": "A", "target": "B", "relation": "calls", "invalid_at": None},
        {"source": "A", "target": "C", "relation": "calls", "invalid_at": "2026-01-01T00:00:00+00:00"},
    ]
    elements = build_graph_elements(entities, relationships)
    assert len(elements["edges"]) == 1
