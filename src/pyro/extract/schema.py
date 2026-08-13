"""Loose extraction schema (plan.md 'Prompt 1: Generic Fact Extraction')."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    canonical_name: str
    domain_tags: list[str] = Field(default_factory=list)
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    patterns_and_concepts: list[str] = Field(default_factory=list)


class Integration(BaseModel):
    source: str
    target: str
    relationship_type: str


class ExtractedFacts(BaseModel):
    is_architectural: bool
    primary_entities: list[Entity] = Field(default_factory=list)
    system_integrations: list[Integration] = Field(default_factory=list)
    evolution_notes: list[str] = Field(default_factory=list)


def merge_facts(facts_list: list[ExtractedFacts]) -> ExtractedFacts:
    """Merge per-chunk extraction results for one article, deduping entities by canonical_name."""
    if not facts_list:
        return ExtractedFacts(is_architectural=False)

    is_architectural = any(f.is_architectural for f in facts_list)

    entities_by_name: dict[str, Entity] = {}
    for facts in facts_list:
        for entity in facts.primary_entities:
            key = entity.canonical_name.strip().lower()
            if key not in entities_by_name:
                entities_by_name[key] = entity
            else:
                existing = entities_by_name[key]
                merged = existing.model_copy(
                    update={
                        "domain_tags": _dedup(existing.domain_tags + entity.domain_tags),
                        "tech_stack": _dedup(existing.tech_stack + entity.tech_stack),
                        "patterns_and_concepts": _dedup(
                            existing.patterns_and_concepts + entity.patterns_and_concepts
                        ),
                        "description": existing.description or entity.description,
                    }
                )
                entities_by_name[key] = merged

    integrations: list[Integration] = []
    seen_integrations: set[tuple[str, str, str]] = set()
    for facts in facts_list:
        for integration in facts.system_integrations:
            key = (integration.source, integration.target, integration.relationship_type)
            if key not in seen_integrations:
                seen_integrations.add(key)
                integrations.append(integration)

    evolution_notes: list[str] = []
    for facts in facts_list:
        for note in facts.evolution_notes:
            if note not in evolution_notes:
                evolution_notes.append(note)

    return ExtractedFacts(
        is_architectural=is_architectural,
        primary_entities=list(entities_by_name.values()),
        system_integrations=integrations,
        evolution_notes=evolution_notes,
    )


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
