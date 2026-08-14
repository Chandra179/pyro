"""Loads prompt templates from the top-level prompts/ directory.

Prompts live as plain .md files outside src/ so they can be edited (and, later,
managed from a dashboard) without touching pipeline code. Which file backs each
stage is configurable (see PromptsConfig in pyro.config), and edits to a prompt
file take effect on the next call — no process restart needed.
"""

from __future__ import annotations

from pathlib import Path

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
