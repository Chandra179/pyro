"""Entity/relationship extraction schema: what systems an article says exist, and how they
relate — the raw material a later graph-merge pass (see docs/architecture.md once rewritten)
resolves across articles into one company-wide diagram."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from pyro.config import Settings

# Reused as a tag on entities/relationships, not a classifier anymore — kept specifically so a
# later cross-company comparison feature has a shared axis to align topics on across companies.
DOMAINS: list[str] = Settings().domains

EntityKind = Literal["service", "datastore", "queue", "external_system", "team"]


class ExtractedEntity(BaseModel):
    name: str
    kind: EntityKind = "service"
    domain: str = "Other"


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    relation: str
    as_of: str | None = None


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
            key = (rel.source.strip().lower(), rel.target.strip().lower(), rel.relation.strip().lower())
            if key[0] and key[1] and key not in relationships:
                relationships[key] = rel

    return ExtractedGraph(entities=list(entities.values()), relationships=list(relationships.values()))
