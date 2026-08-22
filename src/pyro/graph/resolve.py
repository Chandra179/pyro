"""Deterministic entity-name resolution — the cheap pass that runs before the merge LLM call.

Answers the easy cases with string matching (rapidfuzz) and hands the merge pass only the residue
that genuinely needs judgement: an article whose entities all resolve deterministically skips the
LLM call entirely, and when a call *is* needed it's shown only the existing names most similar to
the unresolved entities, not the company's whole entity list.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


class ResolvedName(NamedTuple):
    """One article name's resolution. `method` ("exact"/"fuzzy:<score>"/"llm") is an audit trail
    for how it was decided — persisted per-alias in db/entities.py rather than discarded."""

    canonical: str
    method: str

# Short names ("S3", "EC2", "ELB") differ by one or two characters between genuinely distinct
# systems, so fuzzy matching is only trusted above this length; shorter must match exactly or LLM.
_MIN_FUZZY_LENGTH = 5

# Generic descriptions like "the new microservice" — two unrelated articles can independently
# produce the same phrase, so string matching would silently conflate them; always route to the
# LLM instead. Requires qualifier + noun together — qualifier alone misfires on "New Relic".
_GENERIC_QUALIFIER = r"(new|old|legacy|current|existing|updated|original|previous)"
_GENERIC_NOUN = (
    r"(service|microservice|api|system|gateway|layer|app|application|component|"
    r"database|db|pipeline|engine|cluster|worker|endpoint|module|tool)"
)
_GENERIC_NAME_RE = re.compile(
    rf"^(the |a |an |this |that )?{_GENERIC_QUALIFIER}\b.*\b{_GENERIC_NOUN}\b",
    re.IGNORECASE,
)


def _is_generic(name: str) -> bool:
    return bool(_GENERIC_NAME_RE.match(name.strip()))

# token_sort_ratio, not WRatio: WRatio's partial_ratio would score "Kafka" vs "Kafka Connect" as
# near-perfect, collapsing two different systems.
_SCORER = fuzz.token_sort_ratio


def normalize(name: str) -> str:
    """Casefold and collapse punctuation/whitespace, for exact-match comparison."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class KnownNames:
    """Incrementally-maintained index of a company's existing entity names.

    Built once per merge run and updated via `add()` as new canonical names appear, so each
    article's resolution cost is proportional to its own entity count, not a re-fetch of the
    whole graph — needed since each article must see names resolved earlier in the same run.
    """

    def __init__(self, names: list[str]) -> None:
        self._by_normalized: dict[str, str] = {}
        for name in names:
            self.add(name)

    @property
    def names(self) -> list[str]:
        return list(self._by_normalized.values())

    def add(self, name: str) -> None:
        key = normalize(name)
        if key and key not in self._by_normalized:
            self._by_normalized[key] = name

    def resolve(
        self, article_names: list[str], threshold: int = 92
    ) -> tuple[dict[str, ResolvedName], list[str]]:
        """Match `article_names` against the index without a model.

        Returns `(mapping, unresolved)`; `unresolved` lists names that need the merge LLM. Two
        tiers: exact on the normalized form, then fuzzy at or above `threshold` for names of at
        least _MIN_FUZZY_LENGTH characters. A name matching nothing is left unresolved rather
        than assumed new — that judgement is what the model is for. A generic/relative name
        (`_is_generic`) always skips straight to unresolved even on a would-be match.
        """
        fuzzy_pool = list(self._by_normalized)

        mapping: dict[str, ResolvedName] = {}
        unresolved: list[str] = []

        for name in article_names:
            key = normalize(name)
            if not key:
                continue

            if _is_generic(name):
                unresolved.append(name)
                continue

            exact = self._by_normalized.get(key)
            if exact is not None:
                mapping[name] = ResolvedName(canonical=exact, method="exact")
                continue

            if len(key) >= _MIN_FUZZY_LENGTH and fuzzy_pool:
                match = process.extractOne(
                    key, fuzzy_pool, scorer=_SCORER, score_cutoff=threshold
                )
                if match is not None:
                    canonical = self._by_normalized[match[0]]
                    logger.debug(
                        "fuzzy-resolved %r -> %r (score %.1f)", name, canonical, match[1]
                    )
                    mapping[name] = ResolvedName(
                        canonical=canonical, method=f"fuzzy:{match[1]:.0f}"
                    )
                    continue

            unresolved.append(name)

        return mapping, unresolved


def resolve_known_names(
    article_names: list[str],
    existing_names: list[str],
    threshold: int = 92,
) -> tuple[dict[str, ResolvedName], list[str]]:
    """One-off form of `KnownNames.resolve` for callers that don't need the index kept around."""
    return KnownNames(existing_names).resolve(article_names, threshold)


def candidate_names(
    unresolved: list[str],
    existing_names: list[str],
    limit: int | None = 40,
) -> list[str]:
    """The existing names worth showing the merge prompt for `unresolved`.

    Returns every existing name when there are fewer than `limit` of them; past that, returns the
    `limit` names most similar to the unresolved entities so prompt size stays flat as the graph
    grows. `limit=None` disables the cap.
    """
    if limit is None or len(existing_names) <= limit:
        return sorted(existing_names)
    if not unresolved:
        return []

    by_normalized = {normalize(n): n for n in existing_names}
    pool = list(by_normalized)
    # Spread the budget across entities so one with many near-matches can't crowd out the rest.
    per_entity = max(1, limit // len(unresolved))

    selected: set[str] = set()
    for name in unresolved:
        for match in process.extract(
            normalize(name), pool, scorer=_SCORER, limit=per_entity
        ):
            selected.add(by_normalized[match[0]])
    return sorted(selected)
