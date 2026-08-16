"""Shared primitives used by both synth/structured.py (domain batch synthesis) and
synth/freeform.py (per-article routing synthesis)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyro.config import Settings


@dataclass
class SynthesisContext:
    """The three values every call in a synthesis run shares — bundled so functions take
    one param instead of company_name/settings/model_params separately each time."""

    company_name: str
    settings: Settings
    model_params: dict


@dataclass
class PromptPair:
    system: str
    user: str


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "other"


def first_heading(content: str) -> str:
    return next(
        (
            line.lstrip("#").strip()
            for line in content.splitlines()
            if line.startswith("#")
        ),
        "",
    )


# Some models wrap the whole markdown document in an outer ```markdown fence
# (as if it were a code block rather than the document itself), which breaks
# rendering (headers/Mermaid diagrams show as one inert code block instead).
_OUTER_FENCE_RE = re.compile(r"\A```(?:markdown|md)?\s*\n(.*)\n```\s*\Z", re.DOTALL)


def strip_outer_markdown_fence(text: str) -> str:
    match = _OUTER_FENCE_RE.match(text.strip())
    return match.group(1) if match else text
