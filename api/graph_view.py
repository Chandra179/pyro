"""Builds Mermaid flowchart source from a company's stored entity graph, for the dashboard's
Graph view (api/main.py, dashboard/templates/partials/data_panel.html).

v1 is one static whole-company diagram, not an interactive graph UI — filtering/zoom is a later
iteration. The one filter applied here (dropping "team" entities) is a rendering-only choice:
teams are almost always one-off per-article author credits that don't recur across a company's
blog history, so they add clutter to a systems diagram without carrying diagram-relevant
information. They're still stored and returned by list_entities for any future use — this only
affects what gets drawn.
"""

from __future__ import annotations

import re

_SHAPE_BY_KIND = {
    "datastore": ("[(", ")]"),
    "queue": ("{{", "}}"),
    "external_system": ("([", "])"),
}
_DEFAULT_SHAPE = ("[", "]")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "x"


def _label(text: str) -> str:
    return text.replace('"', "'")


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
        lines.append(f'  {_slug(entity["name"])}{open_}"{_label(entity["name"])}"{close}')

    for rel in relationships:
        if rel["source"] not in visible or rel["target"] not in visible:
            continue
        label = _label(rel["relation"]) + (f" ({rel['as_of']})" if rel.get("as_of") else "")
        lines.append(f'  {_slug(rel["source"])} -->|"{label}"| {_slug(rel["target"])}')

    return "\n".join(lines)
