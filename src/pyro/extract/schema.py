"""Entity/relationship extraction schema: what systems an article says exist, and how they
relate — the raw material a later graph-merge pass (see docs/architecture.md once rewritten)
resolves across articles into one company-wide diagram."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from pyro.config import Settings

logger = logging.getLogger(__name__)

# Just a tag now, not a classifier — kept as a shared axis for a future cross-company comparison.
DOMAINS: list[str] = Settings().domains

EntityKind = Literal["service", "datastore", "queue", "external_system", "library", "model", "team"]

# Controlled vocabulary for relationship predicates: `relation` used to be free text, so "writes
# to"/"persists to"/"stores data in" became distinct edges between the same nodes. The model's
# original wording isn't discarded — normalize_relation moves it to `relation_phrase`.
RelationKind = Literal[
    "calls",
    "routes_to",
    "reads_from",
    "writes_to",
    "publishes_to",
    "subscribes_to",
    "depends_on",
    "composes",
    "replaced_by",
    "deployed_on",
    "deploys",
    "caches",
    "owns",
    "authenticates_with",
    "replicates_to",
    "monitors",
    "derived_from",
]

RELATION_KINDS: list[str] = list(RelationKind.__args__)

# Maps phrasings models emit onto the vocabulary; keys matched after lowercasing and collapsing
# non-alphanumerics to spaces, as a *prefix* — phrases often carry a trailing qualifier ("uses for
# distributed tracing"), and whole-string matching once sent 21 of 26 edges to the fallback.
_RELATION_SYNONYMS: dict[str, str] = {
    "calls": "calls",
    "invokes": "calls",
    "requests": "calls",
    "sends requests to": "calls",
    "sends traffic to": "calls",
    "talks to": "calls",
    "communicates with": "calls",
    "queries": "reads_from",
    "routes": "routes_to",
    "routes requests to": "routes_to",
    "routes to": "routes_to",
    "proxies": "routes_to",
    "proxies calls to": "routes_to",
    "forwards": "routes_to",
    "load balances": "routes_to",
    "dispatches to": "routes_to",
    "reads from": "reads_from",
    "reads": "reads_from",
    "loads from": "reads_from",
    "fetches": "reads_from",
    "fetches from": "reads_from",
    "downloads": "reads_from",
    "pulls": "reads_from",
    "consumes from": "reads_from",
    "retrieves": "reads_from",
    "writes to": "writes_to",
    "writes": "writes_to",
    "persists to": "writes_to",
    "stores data in": "writes_to",
    "stores in": "writes_to",
    "stores": "writes_to",
    "saves to": "writes_to",
    "ingests into": "writes_to",
    "indexes into": "writes_to",
    "publishes to": "publishes_to",
    "publishes": "publishes_to",
    "produces to": "publishes_to",
    "produces": "publishes_to",
    "emits to": "publishes_to",
    "emits": "publishes_to",
    "pushes": "publishes_to",
    "sends events to": "publishes_to",
    "subscribes to": "subscribes_to",
    "subscribes": "subscribes_to",
    "consumes": "subscribes_to",
    "listens to": "subscribes_to",
    "depends on": "depends_on",
    "uses": "depends_on",
    "relies on": "depends_on",
    "built on": "depends_on",
    "integrates with": "depends_on",
    "leverages": "depends_on",
    "includes": "composes",
    "composes": "composes",
    "aggregates": "composes",
    "comprises": "composes",
    "contains": "composes",
    "consists of": "composes",
    "made up of": "composes",
    "part of": "composes",
    "replaced by": "replaced_by",
    "replaces": "replaced_by",
    "migrated to": "replaced_by",
    "migrates": "replaced_by",
    "succeeded by": "replaced_by",
    "deprecated by": "replaced_by",
    "deployed on": "deployed_on",
    "runs on": "deployed_on",
    "hosted on": "deployed_on",
    "scheduled on": "deployed_on",
    "deploys": "deploys",
    "provisions": "deploys",
    "orchestrates": "deploys",
    "schedules": "deploys",
    "caches": "caches",
    "cached by": "caches",
    "fronts": "caches",
    "owns": "owns",
    "maintains": "owns",
    "operates": "owns",
    "built by": "owns",
    "developed by": "owns",
    "hosts": "owns",
    "authenticates with": "authenticates_with",
    "authenticates via": "authenticates_with",
    "authorizes with": "authenticates_with",
    "replicates to": "replicates_to",
    "syncs to": "replicates_to",
    "mirrors to": "replicates_to",
    "monitors": "monitors",
    "observes": "monitors",
    "traces": "monitors",
    "alerts on": "monitors",
    "captures": "monitors",
    "instruments": "monitors",
    "derived from": "derived_from",
    "computed from": "derived_from",
    "generated from": "derived_from",
    "trained on": "derived_from",
}

# Longest first, so "routes requests to" is tried before the bare "routes" and a more specific
# reading always wins over a more general one.
_SYNONYMS_BY_LENGTH: list[tuple[str, str]] = sorted(
    _RELATION_SYNONYMS.items(), key=lambda kv: -len(kv[0])
)

# Generic enough not to invent a direction-specific claim the article may not support. Used as a
# placeholder when even the LLM fallback tier (extract/relation_resolve.py) can't place a phrase.
_FALLBACK_RELATION = "depends_on"


def _normalize_relation_deterministic(raw: str) -> tuple[str, bool]:
    """The free, synchronous tier: exact vocabulary match, then exact synonym, then a synonym
    matched as the phrase's leading words (dropping a trailing qualifier).

    Returns `(canonical, matched)` — `matched=False` means nothing fit and `canonical` is just
    `_FALLBACK_RELATION` standing in until the LLM fallback tier gets a chance at it (see
    `ExtractedRelationship.needs_relation_review` / `extract/relation_resolve.py`).
    """
    collapsed = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    if not collapsed:
        return _FALLBACK_RELATION, False

    if collapsed.replace(" ", "_") in RELATION_KINDS:
        return collapsed.replace(" ", "_"), True

    exact = _RELATION_SYNONYMS.get(collapsed)
    if exact is not None:
        return exact, True

    for phrase, canonical in _SYNONYMS_BY_LENGTH:
        # Word-boundary prefix match: "uses for distributed tracing" -> "uses", but "userland" is
        # not matched by "use".
        if collapsed.startswith(phrase) and (
            len(collapsed) == len(phrase) or collapsed[len(phrase)] == " "
        ):
            return canonical, True

    return _FALLBACK_RELATION, False


def normalize_relation(raw: str) -> str:
    """Public, deterministic-only entry point — used by `ExtractedRelationship`'s validator (which
    must stay synchronous so extraction is offline-testable) and by `graph/backfill.py`'s one-off
    vocabulary rewrite. Callers that can afford an async LLM call for what this tier misses should
    use `extract/relation_resolve.py`'s `apply_relation_fallback` instead/in addition — this
    function alone always resolves to something (falling back silently otherwise), so use it
    directly only when that fallback is an acceptable final answer.
    """
    canonical, matched = _normalize_relation_deterministic(raw)
    if not matched:
        # Falling back is silent to callers by design; log so vocabulary drift stays visible.
        logger.warning(
            "relation %r matched no vocabulary/synonym entry; falling back to %r", raw, canonical
        )
    return canonical


class ExtractedEntity(BaseModel):
    name: str
    kind: EntityKind = "service"
    domain: str = "Other"
    # Disambiguator, most useful when `name` isn't a real proper noun — see graph/resolve.py.
    description: str | None = None


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    relation: RelationKind
    # Model's original wording, kept when it differed from the canonical predicate.
    relation_phrase: str | None = None
    as_of: str | None = None
    # True when the deterministic tier found no match, so `relation` is just `_FALLBACK_RELATION` —
    # extract/relation_resolve.py's LLM tier resolves these after an article's chunks are parsed.
    # Excluded from model_dump(): a same-process signal between the two tiers, nothing downstream
    # needs it.
    needs_relation_review: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_relation(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("relation")
        if not isinstance(raw, str):
            return data
        canonical, matched = _normalize_relation_deterministic(raw)
        if not matched:
            logger.warning(
                "relation %r matched no vocabulary/synonym entry; falling back to %r pending "
                "LLM review",
                raw,
                canonical,
            )
        data = {**data, "relation": canonical, "needs_relation_review": not matched}
        if data.get("relation_phrase") is None and raw.strip() != canonical:
            data["relation_phrase"] = raw.strip()
        return data


class ExtractedGraph(BaseModel):
    entities: list[ExtractedEntity] = []
    relationships: list[ExtractedRelationship] = []


def merge_graph_chunks(chunks: list[ExtractedGraph]) -> ExtractedGraph:
    """Merge per-chunk extraction results for one article: dedup entities by case-insensitive
    name, relationships by (source, target, relation). Reconciling names *across* articles is
    the graph-merge pass's job, not this one."""
    entities: dict[str, ExtractedEntity] = {}
    for chunk in chunks:
        for entity in chunk.entities:
            key = entity.name.strip().lower()
            if key and key not in entities:
                entities[key] = entity

    relationships: dict[tuple[str, str, str], ExtractedRelationship] = {}
    for chunk in chunks:
        for rel in chunk.relationships:
            key = (rel.source.strip().lower(), rel.target.strip().lower(), rel.relation)
            if key[0] and key[1] and key not in relationships:
                relationships[key] = rel

    return ExtractedGraph(entities=list(entities.values()), relationships=list(relationships.values()))
