"""Builds React Flow graph elements from a company's stored entity graph, for the dashboard's
Graph view (api/main.py, dashboard/templates/partials/_panel_graph.html).

Rendered client-side as an interactive React Flow graph (see
dashboard/static/src/graph/GraphIsland.jsx) — pan, zoom, node dragging, and per-domain
expand/collapse are handled there; this module's only job is shaping stored
entities/relationships into a flat `{"nodes": [...], "edges": [...]}` dict. The data stays in
ArangoDB (list_entities/list_relationships return everything) for any future use; the filters
below are rendering-only choices:

- Dropping "team" entities: teams are almost always one-off per-article author credits that don't
  recur across a company's blog history, so they add clutter without carrying diagram-relevant
  information.
- Dropping edges with `invalid_at` set: those describe behavior of a system after a later article
  says it was replaced (see graph/merge.py's `replaced_by` handling / db/relationships.py's
  `invalidate_outgoing`) — still true historically, but not part of the *current* system map this
  diagram is meant to show. A future "show historical" toggle can surface them; the default view
  shouldn't draw decommissioned behavior as if it's live.

`domain` rides along on every node because it's the grouping key GraphIsland's expand/collapse
feature clusters on (config/config.yaml's fixed domain taxonomy, stamped on every entity at
extraction time) — falls back to "Other" for anything untagged, matching the taxonomy's own
required fallback value.
"""

from __future__ import annotations

from pyro.db import slug


def _clean_label(text: str) -> str:
    """Entity/relation names come from an LLM reading arbitrary blog prose — collapse embedded
    newlines so a label never breaks a single-line node/edge text layout."""
    return " ".join(text.split()) or "?"


def _humanize(relation: str) -> str:
    """Canonical predicates are stored snake_case (extract.schema.RelationKind); edge labels read
    better with spaces."""
    return relation.replace("_", " ")


def build_graph_elements(entities: list[dict], relationships: list[dict]) -> dict:
    """Returns {"nodes": [...], "edges": [...]}, or {"nodes": [], "edges": []} if there's nothing
    renderable (no non-team entities) — callers should treat that as "no diagram yet", same as an
    empty entity list."""
    visible = {e["name"] for e in entities if e.get("kind") != "team"}
    if not visible:
        return {"nodes": [], "edges": []}

    nodes: list[dict] = []
    for entity in entities:
        if entity["name"] not in visible:
            continue
        nodes.append(
            {
                "id": slug(entity["name"]),
                "label": _clean_label(entity["name"]),
                "kind": entity.get("kind", "service"),
                "domain": entity.get("domain") or "Other",
            }
        )

    edges: list[dict] = []
    for i, rel in enumerate(relationships):
        if rel.get("invalid_at"):
            continue
        if rel["source"] not in visible or rel["target"] not in visible:
            continue
        label = _clean_label(_humanize(rel["relation"]))
        if rel.get("as_of"):
            label += f" ({_clean_label(str(rel['as_of']))})"
        edges.append(
            {
                "id": f"__edge_{i}",
                "source": slug(rel["source"]),
                "target": slug(rel["target"]),
                "label": label,
            }
        )

    return {"nodes": nodes, "edges": edges}
