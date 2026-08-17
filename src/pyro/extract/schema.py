"""Entity/relationship extraction schema: what systems an article says exist, and how they
relate — the raw material a later graph-merge pass (see docs/architecture.md once rewritten)
resolves across articles into one company-wide diagram."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from pyro.config import Settings

# Reused as a tag on entities/relationships, not a classifier anymore — kept specifically so a
# later cross-company comparison feature has a shared axis to align topics on across companies.
DOMAINS: list[str] = Settings().domains

EntityKind = Literal["service", "datastore", "queue", "external_system", "team"]

# Controlled vocabulary for relationship predicates.
#
# `kind` and `domain` have always been constrained; `relation` used to be free text straight off
# the model, which meant "writes to", "writes-to", "persists to" and "stores data in" became four
# distinct edges between the same pair of nodes — Database.upsert_relationship keys on the
# relation, so synonyms accumulated as duplicate edges and cluttered every rendered diagram.
# Constraining the predicate is what makes an edge between two systems mean one thing.
#
# The model's own wording is not discarded: normalize_relation below moves it to `relation_phrase`,
# stored on the edge as a non-key property for display and for auditing a canonicalization.
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

# Maps the phrasings models actually emit onto the vocabulary. Keys are matched against the
# model's `relation` after lowercasing and collapsing non-alphanumerics to single spaces, so one
# entry covers "writes-to", "Writes To" and "writes_to" alike.
#
# Entries are ordered longest-first at match time, and matched as a *prefix* of the phrase (see
# normalize_relation), because the dominant real-world shape is a known predicate carrying a
# trailing qualifier: "uses for distributed tracing", "downloads rule sets from", "hosts migrated
# route handlers from". Matching only whole strings sent all of those to the fallback, which is how
# a first pass over a real graph ended up with 21 of 26 edges reading `depends_on`.
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

# Generic enough to be honest about "these two systems are connected, in an unrecognized way"
# without inventing a direction-specific claim the article may not support. Reaching this should
# be rare — if it stops being rare, the vocabulary or the synonym table is missing something real.
_FALLBACK_RELATION = "depends_on"


def normalize_relation(raw: str) -> str:
    """Map a model-emitted predicate onto RelationKind.

    Tiers, in order: an exact vocabulary value passes through; an exact synonym is rewritten; a
    synonym appearing as the leading words of the phrase is rewritten (dropping the trailing
    qualifier); anything left becomes _FALLBACK_RELATION.
    """
    collapsed = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    if not collapsed:
        return _FALLBACK_RELATION

    if collapsed.replace(" ", "_") in RELATION_KINDS:
        return collapsed.replace(" ", "_")

    exact = _RELATION_SYNONYMS.get(collapsed)
    if exact is not None:
        return exact

    for phrase, canonical in _SYNONYMS_BY_LENGTH:
        # Word-boundary prefix match: "uses for distributed tracing" -> "uses", but "userland" is
        # not matched by "use".
        if collapsed.startswith(phrase) and (
            len(collapsed) == len(phrase) or collapsed[len(phrase)] == " "
        ):
            return canonical

    return _FALLBACK_RELATION


class ExtractedEntity(BaseModel):
    name: str
    kind: EntityKind = "service"
    domain: str = "Other"


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    relation: RelationKind
    # The model's original wording, kept whenever it differed from the canonical predicate — so
    # narrowing to the vocabulary stays reversible and inspectable rather than lossy.
    relation_phrase: str | None = None
    as_of: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_relation(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("relation")
        if not isinstance(raw, str):
            return data
        canonical = normalize_relation(raw)
        data = {**data, "relation": canonical}
        if data.get("relation_phrase") is None and raw.strip() != canonical:
            data["relation_phrase"] = raw.strip()
        return data


class ExtractedGraph(BaseModel):
    entities: list[ExtractedEntity] = []
    relationships: list[ExtractedRelationship] = []


def merge_graph_chunks(chunks: list[ExtractedGraph]) -> ExtractedGraph:
    """Merge per-chunk extraction results for one article. Entities are deduped by
    case-insensitive name and relationships by (source, target, relation) — cheap, since within
    one article the same system is almost always mentioned with the same name across its own
    chunks. Reconciling names *across different articles* is a separate, harder concern handled
    by the graph-merge pass, not here."""
    entities: dict[str, ExtractedEntity] = {}
    for chunk in chunks:
        for entity in chunk.entities:
            key = entity.name.strip().lower()
            if key and key not in entities:
                entities[key] = entity

    relationships: dict[tuple[str, str, str], ExtractedRelationship] = {}
    for chunk in chunks:
        for rel in chunk.relationships:
            # `relation` is already canonical here (validated onto RelationKind), so two chunks
            # phrasing the same edge differently now collapse into one instead of surviving as
            # near-duplicate edges.
            key = (rel.source.strip().lower(), rel.target.strip().lower(), rel.relation)
            if key[0] and key[1] and key not in relationships:
                relationships[key] = rel

    return ExtractedGraph(entities=list(entities.values()), relationships=list(relationships.values()))
