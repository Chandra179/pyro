"""Wraps Mermaid diagram source in the markup mermaid.js looks for client-side.

This used to also host a full markdown -> HTML renderer (`render_markdown`, with a
pymdownx.superfences custom fence) for the prose synthesis stage. That stage was replaced by the
graph merge, and nothing has called it since — it and its `markdown`/`pymdown-extensions`
dependencies are gone. What remains is the one line the graph view actually needs.
"""

from __future__ import annotations

import html


def render_mermaid(source: str) -> str:
    """Wrap raw Mermaid source in the `<pre class="mermaid">` block that mermaid.min.js scans for
    and replaces with an inline SVG (see dashboard/static/js/app.js). `not-prose` opts the block
    out of Tailwind Typography's styling, which would otherwise restyle the rendered diagram."""
    return f'<pre class="not-prose mermaid">{html.escape(source)}</pre>'
