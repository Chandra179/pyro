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

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# Fuzzy matching is only trusted for names long enough for a high score to be meaningful. Short
# names ("S3", "EC2", "ELB") differ by one or two characters between genuinely distinct systems,
# so they must match exactly or go to the model.
_MIN_FUZZY_LENGTH = 5

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
    ) -> tuple[dict[str, str], list[str]]:
        """Match `article_names` against the index without a model.

        Returns `(mapping, unresolved)` where `mapping` sends an article's name to the canonical
        existing name it matched, and `unresolved` lists the names that need the merge LLM.

        Matching runs in two tiers: exact on the normalized form, then fuzzy at or above
        `threshold` for names of at least _MIN_FUZZY_LENGTH characters. An article name that
        matches nothing is left unresolved rather than assumed new — deciding "this is a
        genuinely new system" is exactly the judgement the model is for.
        """
        # Fuzzy candidates are the normalized keys; mapping back through _by_normalized recovers
        # the canonical spelling to actually store.
        fuzzy_pool = list(self._by_normalized)

        mapping: dict[str, str] = {}
        unresolved: list[str] = []

        for name in article_names:
            key = normalize(name)
            if not key:
                continue

            exact = self._by_normalized.get(key)
            if exact is not None:
                mapping[name] = exact
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
                    mapping[name] = canonical
                    continue

            unresolved.append(name)

        return mapping, unresolved


def resolve_known_names(
    article_names: list[str],
    existing_names: list[str],
    threshold: int = 92,
) -> tuple[dict[str, str], list[str]]:
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
