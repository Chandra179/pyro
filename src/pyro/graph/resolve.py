"""Deterministic entity-name resolution — the cheap pass that runs before the merge LLM call.

Most entities an article mentions are systems the graph already knows about, named identically:
an article saying "Kafka" when "Kafka" is already an entity does not need a model to notice. The
old merge pass sent every article's full entity list to an LLM unconditionally, so a run over a
200-post blog paid 200 calls to mostly restate names verbatim.

This module answers the easy cases with string matching (rapidfuzz), and hands the merge pass only
the residue that genuinely needs judgement. Two consequences beyond cost:

  - An article whose entities all resolve deterministically skips the LLM call entirely.
  - When a call *is* needed, it is shown the existing names most similar to the unresolved
    entities rather than the company's entire entity list, which used to grow without bound and
    made the prompt scale linearly with graph size.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


class ResolvedName(NamedTuple):
    """One article name's resolution, with *how* it was decided kept alongside the result.

    `method` is one of "exact", "fuzzy:<score>" (from this module), or "llm" (from
    graph/merge.py's model tier) — an audit trail for what would otherwise be an unrecoverable
    decision the instant `canonical` is written to storage. See db/entities.py, where this is
    persisted per-alias rather than discarded."""

    canonical: str
    method: str

# Fuzzy matching is only trusted for names long enough for a high score to be meaningful. Short
# names ("S3", "EC2", "ELB") differ by one or two characters between genuinely distinct systems,
# so they must match exactly or go to the model.
_MIN_FUZZY_LENGTH = 5

# Relative/generic descriptions an article uses when it never gives a system a real name — "the
# new microservice", "old API service". These are not stable identifiers: two unrelated articles
# describing unrelated migrations can independently produce the exact same phrase. String-matching
# them (exact or fuzzy) against the existing index would silently conflate unrelated systems, so
# names matching this are always routed to the LLM tier regardless of match quality, where
# kind/domain/description context can inform a real decision instead of a coincidence.
#
# Requires a qualifier ("new"/"old"/...) *and* a generic system noun somewhere after it — a
# qualifier alone would misfire on real proper nouns that happen to start with one, like "New
# Relic" or "New York" region names. Over-matching here just means an extra LLM call for a name
# that turns out fine on its own; under-matching is the failure mode that actually corrupts the
# graph, so the noun list is kept broad on purpose.
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

# token_sort_ratio, not WRatio: WRatio folds in partial_ratio, which scores a substring match as
# near-perfect and would happily collapse "Kafka" into "Kafka Connect" — two different systems.
# token_sort_ratio compares the full normalized strings (order-insensitively), so it absorbs
# word-order and punctuation drift without treating a shorter name as equal to a longer one it
# happens to be contained in.
_SCORER = fuzz.token_sort_ratio


def normalize(name: str) -> str:
    """Casefold and collapse punctuation/whitespace, for exact-match comparison."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class KnownNames:
    """Incrementally-maintained index of a company's existing entity names.

    A merge run resolves one article at a time and needs each article to see names resolved by
    prior articles earlier in the same run (see graph/merge.py's docstring on why the loop is
    sequential). The naive way to get that — re-fetch `list_entity_names` and rebuild the
    normalized-name dict from scratch before every article — costs O(articles x existing_entities)
    in both DB round-trips and renormalization as a company's graph grows. Building this once per
    run and calling `add()` as new canonical names appear keeps each article's resolution cost
    proportional to its own entity count, not the whole graph's."""

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

        Returns `(mapping, unresolved)` where `mapping` sends an article's name to a
        `ResolvedName(canonical, method)` — `method` records *how* the match was decided
        ("exact" or "fuzzy:<score>"), not just what it resolved to, so that decision is not lost
        the instant it's made (see `ResolvedName`'s docstring). `unresolved` lists the names that
        need the merge LLM.

        Matching runs in two tiers: exact on the normalized form, then fuzzy at or above
        `threshold` for names of at least _MIN_FUZZY_LENGTH characters. An article name that
        matches nothing is left unresolved rather than assumed new — deciding "this is a
        genuinely new system" is exactly the judgement the model is for. A generic/relative name
        (`_is_generic`) always skips straight to unresolved even on a would-be exact or fuzzy
        match — string equality between two generic phrases isn't evidence they're the same
        system, so this always needs the model's judgement (see `_GENERIC_NAME_RE`'s comment).
        """
        # Fuzzy candidates are the normalized keys; mapping back through _by_normalized recovers
        # the canonical spelling to actually store.
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
    """One-off form of `KnownNames.resolve` for callers that don't need the index kept around
    across multiple calls (tests; anywhere resolving against a fixed, unchanging name list)."""
    return KnownNames(existing_names).resolve(article_names, threshold)


def candidate_names(
    unresolved: list[str],
    existing_names: list[str],
    limit: int | None = 40,
) -> list[str]:
    """The existing names worth showing the merge prompt for `unresolved`.

    Returns every existing name when there are fewer than `limit` of them (the common case for a
    young graph, and strictly better context than a subset). Past that, returns the `limit` names
    most similar to the unresolved entities — a retrieval step, so prompt size stays flat as a
    company's graph grows into the hundreds of entities instead of growing with it.

    `limit=None` disables the cap and always returns everything.
    """
    if limit is None or len(existing_names) <= limit:
        return sorted(existing_names)
    if not unresolved:
        return []

    by_normalized = {normalize(n): n for n in existing_names}
    pool = list(by_normalized)
    # Spread the budget across the unresolved entities so one entity with many near-matches can't
    # crowd the others out of the prompt entirely.
    per_entity = max(1, limit // len(unresolved))

    selected: set[str] = set()
    for name in unresolved:
        for match in process.extract(
            normalize(name), pool, scorer=_SCORER, limit=per_entity
        ):
            selected.add(by_normalized[match[0]])
    return sorted(selected)
