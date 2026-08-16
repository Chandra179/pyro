"""Server-side markdown -> HTML rendering for synthesized docs.

Synthesis prompts (prompts/synthesis/*.md) instruct the model to emit
```mermaid fenced code blocks for architecture diagrams. `pymdownx.superfences`
(pymdown-extensions) lets us register a custom fence formatter for that
language — the same mechanism mkdocs-material uses for mermaid support —
instead of hand-rolling fence detection. It renders straight to
`<pre class="mermaid">...</pre>`, the markup mermaid.js (dashboard/static/js/
mermaid.min.js) scans for client-side and replaces with an inline SVG.
"""

from __future__ import annotations

import html

import markdown as md


def _mermaid_fence(
    source: str, language: str, css_class: str, options: dict, md, **kwargs
) -> str:
    return f'<pre class="not-prose mermaid">{html.escape(source)}</pre>'


_renderer = md.Markdown(
    extensions=["pymdownx.superfences", "tables", "sane_lists"],
    extension_configs={
        "pymdownx.superfences": {
            "custom_fences": [
                {"name": "mermaid", "class": "mermaid", "format": _mermaid_fence},
            ]
        }
    },
)


def render_markdown(content: str) -> str:
    """Render doc markdown to HTML, turning ```mermaid fences into mermaid.js source blocks."""
    _renderer.reset()
    return _renderer.convert(content)
