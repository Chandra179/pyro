"""Builds Mermaid flowchart source from a company's stored entity graph, for the dashboard's
Graph view (api/main.py, dashboard/templates/partials/_panel_graph.html).

v1 is one static whole-company diagram, not an interactive graph UI — filtering/zoom is a later
iteration. The one filter applied here (dropping "team" entities) is a rendering-only choice:
teams are almost always one-off per-article author credits that don't recur across a company's
blog history, so they add clutter to a systems diagram without carrying diagram-relevant
information. They're still stored and returned by list_entities for any future use — this only
affects what gets drawn.
"""

from __future__ import annotations

import re

from pyro.db import slug

_SHAPE_BY_KIND = {
    "datastore": ("[(", ")]"),
    "queue": ("{{", "}}"),
    "external_system": ("([", "])"),
}
_DEFAULT_SHAPE = ("[", "]")

# Characters that terminate or reshape a Mermaid node/edge label even inside quotes. Entity names
# come from an LLM reading arbitrary blog prose, so names like `Search [v2]` and `Feed #3` do turn
# up; left alone they produce a diagram that fails to parse and renders as nothing at all.
_LABEL_UNSAFE = re.compile(r'["\[\]{}()<>|#;]')


def _label(text: str) -> str:
    """Make an arbitrary entity name safe to sit inside a quoted Mermaid label."""
    return _LABEL_UNSAFE.sub(" ", text).replace("\n", " ").strip() or "?"


def _humanize(relation: str) -> str:
    """Canonical predicates are stored snake_case (extract.schema.RelationKind); edge labels read
    better with spaces."""
    return relation.replace("_", " ")


def build_graph_mermaid(entities: list[dict], relationships: list[dict]) -> str:
    """Returns Mermaid `flowchart` source, or "" if there's nothing renderable (no non-team
    entities) — callers should treat that as "no diagram yet", same as an empty entity list."""
    visible = {e["name"] for e in entities if e.get("kind") != "team"}
    if not visible:
        return ""

    lines = ["flowchart LR"]
    for entity in entities:
        if entity["name"] not in visible:
            continue
        open_, close = _SHAPE_BY_KIND.get(entity.get("kind", "service"), _DEFAULT_SHAPE)
        lines.append(f'  {slug(entity["name"])}{open_}"{_label(entity["name"])}"{close}')

    for rel in relationships:
        if rel["source"] not in visible or rel["target"] not in visible:
            continue
        label = _label(_humanize(rel["relation"]))
        if rel.get("as_of"):
            label += f" ({_label(str(rel['as_of']))})"
        lines.append(f'  {slug(rel["source"])} -->|"{label}"| {slug(rel["target"])}')

    return "\n".join(lines)
