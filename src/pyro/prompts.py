"""Loads prompt templates from the top-level prompts/ directory.

Prompts live as plain .md files outside src/ so they can be edited (and, from the dashboard,
chosen per run) without touching pipeline code. Extraction prompts live under
prompts/extraction/<variant>/ (e.g. prompts/extraction/default/system.md) — "variant" is a
growable set of alternate prompt styles for extraction. Which variant backs a given run is
configurable (see build_prompts_config below), which is what the dashboard uses to let a run
pick its extraction template. Edits to a prompt file take effect on the next call — no process
restart needed.

The graph-merge prompt (prompts/merge/) has no variant concept — v1 is a single fixed template.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyro.config import PromptsConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = _REPO_ROOT / "prompts"

_cache: dict[Path, tuple[float, str]] = {}


def load_prompt(relative_path: str) -> str:
    """Read a prompt template, re-reading only if the file changed since last load."""
    path = PROMPTS_DIR / relative_path
    mtime = path.stat().st_mtime
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    text = path.read_text().strip()
    _cache[path] = (mtime, text)
    return text


def list_variants(stage: str = "extraction") -> list[str]:
    """Variant names available for a stage, e.g. list_variants("extraction") -> ["default"]. A
    variant is just a subdirectory of prompts/<stage>/ containing the template files for that
    stage — add a new one by adding a new subdirectory, no code change needed. "default" is
    always listed first when present; the rest are alphabetical."""
    stage_dir = PROMPTS_DIR / stage
    if not stage_dir.is_dir():
        return []
    names = sorted(p.name for p in stage_dir.iterdir() if p.is_dir())
    if "default" in names:
        names.remove("default")
        names.insert(0, "default")
    return names


def build_prompts_config(extraction_variant: str) -> PromptsConfig:
    """Build a PromptsConfig pointing at the given extraction variant — what the dashboard uses
    to let a run pick its extraction template. The merge prompt fields keep their defaults,
    since the merge stage isn't user-selectable in v1."""
    from pyro.config import PromptsConfig

    extraction_dir = f"extraction/{extraction_variant}"
    return PromptsConfig(
        extraction_system=f"{extraction_dir}/system.md",
        extraction_user=f"{extraction_dir}/user.md",
    )
