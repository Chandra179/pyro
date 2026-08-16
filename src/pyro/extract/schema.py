"""Simple extraction schema: what an article is about, the problem, the solution, and its domain."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from pyro.config import Settings

# Fixed domain taxonomy (config/config.yaml: `domains`) so classification stays
# consistent across articles instead of the LLM inventing free-form labels per
# call. "Other" is the required fallback for anything that doesn't fit. Kept
# as a module-level default so callers (and tests) that don't have a Settings
# instance handy still get sane behavior.
DOMAINS: list[str] = Settings().domains


class ExtractedFacts(BaseModel):
    domain: str = "Other"
    topic: str = ""
    problem: str = ""
    solution: str = ""


def merge_facts(
    facts_list: list[ExtractedFacts], domains: list[str] = DOMAINS
) -> ExtractedFacts:
    """Merge per-chunk extraction results for one article."""
    if not facts_list:
        return ExtractedFacts()

    domain = next((f.domain for f in facts_list if f.domain in domains), "Other")

    return ExtractedFacts(
        domain=domain,
        topic=_join_unique(f.topic for f in facts_list),
        problem=_join_unique(f.problem for f in facts_list),
        solution=_join_unique(f.solution for f in facts_list),
    )


def _join_unique(parts: Iterable[str]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            result.append(part)
    return " ".join(result)
