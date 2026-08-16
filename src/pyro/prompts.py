"""Loads prompt templates from the top-level prompts/ directory.

Prompts live as plain .md files outside src/ so they can be edited (and, from the
dashboard, chosen per run) without touching pipeline code. Each stage's prompts live
under prompts/<stage>/<mode>/<variant>/ (e.g. prompts/extraction/structured/default/
system.md) — "variant" is a growable set of alternate prompt styles for that
stage+mode. Which variant backs a given run is configurable (see PromptsConfig in
pyro.config / build_prompts_config below), and edits to a prompt file take effect on
the next call — no process restart needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pyro.config import PromptsConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = _REPO_ROOT / "prompts"

PipelineMode = Literal["structured", "freeform"]

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


def list_variants(stage: Literal["extraction", "synthesis"], mode: PipelineMode) -> list[str]:
    """Variant names available for a stage+mode, e.g. list_variants("synthesis", "structured")
    -> ["default"]. A variant is just a subdirectory of prompts/<stage>/<mode>/ containing the
    template files for that stage — add a new one by adding a new subdirectory, no code change
    needed. "default" is always listed first when present; the rest are alphabetical."""
    stage_dir = PROMPTS_DIR / stage / mode
    if not stage_dir.is_dir():
        return []
    names = sorted(p.name for p in stage_dir.iterdir() if p.is_dir())
    if "default" in names:
        names.remove("default")
        names.insert(0, "default")
    return names


def build_prompts_config(
    mode: PipelineMode, extraction_variant: str, synthesis_variant: str
) -> "PromptsConfig":
    """Build a PromptsConfig pointing at the given (mode, variant) choice for each stage —
    what the dashboard uses to let a run pick its extraction/synthesis templates independently.
    Only the fields relevant to `mode` are set; the other mode's fields keep their defaults
    since the pipeline only ever reads the fields for the mode it's actually running."""
    from pyro.config import PromptsConfig

    extraction_dir = f"extraction/{mode}/{extraction_variant}"
    synthesis_dir = f"synthesis/{mode}/{synthesis_variant}"
    if mode == "structured":
        return PromptsConfig(
            extraction_system=f"{extraction_dir}/system.md",
            extraction_user=f"{extraction_dir}/user.md",
            synthesis_system=f"{synthesis_dir}/system.md",
            synthesis_user=f"{synthesis_dir}/user.md",
            synthesis_batch_system=f"{synthesis_dir}/batch_system.md",
            synthesis_batch_user=f"{synthesis_dir}/batch_user.md",
        )
    return PromptsConfig(
        extraction_freeform_system=f"{extraction_dir}/system.md",
        extraction_freeform_user=f"{extraction_dir}/user.md",
        synthesis_freeform_route_system=f"{synthesis_dir}/route_system.md",
        synthesis_freeform_route_user=f"{synthesis_dir}/route_user.md",
    )
