"""Wraps React Flow graph elements in the markup app.js scans for client-side.

This used to also host a full markdown -> HTML renderer (`render_markdown`, with a
pymdownx.superfences custom fence) for the prose synthesis stage. That stage was replaced by the
graph merge, and nothing has called it since — it and its `markdown`/`pymdown-extensions`
dependencies are gone. What remains is the one function the graph view actually needs.
"""

from __future__ import annotations

import html
import json


def render_react_flow(elements: dict) -> str:
    """Wrap a {"nodes": [...], "edges": [...]} dict (api/graph_view.py's build_graph_elements) in
    the `<div class="react-flow-graph">` block static/js/app.js scans for and turns into an
    interactive pan/zoom/drag/expand-collapse graph (dashboard/static/src/graph/GraphIsland.jsx).
    The elements ride along as a JSON-encoded data attribute (rather than a separate <script> tag)
    so the whole block — data included — swaps cleanly via htmx outerHTML like the rest of the
    panel."""
    return f'<div class="react-flow-graph not-prose" data-elements="{html.escape(json.dumps(elements))}"></div>'
