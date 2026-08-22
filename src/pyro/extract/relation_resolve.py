"""LLM fallback tier for relation-predicate canonicalization.

Mirrors graph/resolve.py's two-tier shape for entity names: a free deterministic pass runs first
(extract/schema.py's normalize_relation, inside ExtractedRelationship's validator), and only
phrases that tier couldn't place at all reach this module's single batched LLM call. Runs after an
article's chunks are parsed and merged (extract/pipeline.py's extract_article), not inside the
pydantic validator — that keeps extraction's schema validation synchronous and offline-testable,
and keeps the LLM cost to at most one call per article instead of one per unmatched edge.
"""

from __future__ import annotations

import json
import logging

from json_repair import repair_json
from litellm import acompletion
from pydantic import BaseModel, ValidationError

from pyro.config import Settings
from pyro.extract.prompts import (
    relation_resolve_system_prompt,
    relation_resolve_user_prompt,
)
from pyro.extract.schema import RELATION_KINDS, ExtractedGraph
from pyro.router import call_with_rate_limit_retry

logger = logging.getLogger(__name__)


class _ResolvedPhrase(BaseModel):
    phrase: str
    canonical: str | None = None


class _RelationResolutionResponse(BaseModel):
    resolved: list[_ResolvedPhrase] = []


async def _call_relation_resolver(
    phrases: list[str], settings: Settings, model_params: list[dict]
) -> dict[str, str]:
    """One cascade-backed call mapping as many of `phrases` onto RELATION_KINDS as the model is
    confident about. A phrase absent from the returned mapping (model said null, or every tier
    failed) is left for the caller's existing deterministic fallback."""
    system = relation_resolve_system_prompt(settings)
    user = relation_resolve_user_prompt(settings).format(
        relations="\n".join(f"- {r}" for r in RELATION_KINDS),
        phrases_json=json.dumps(phrases, indent=2),
    )
    for params in model_params:
        try:
            response = await call_with_rate_limit_retry(
                lambda params=params: acompletion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    **params,
                ),
                settings,
            )
            parsed = _RelationResolutionResponse.model_validate(
                repair_json(response.choices[0].message.content, return_objects=True)
            )
            return {
                item.phrase: item.canonical
                for item in parsed.resolved
                if item.canonical in RELATION_KINDS
            }
        except (Exception, ValidationError) as exc:
            logger.warning("relation-resolve model %s failed: %s", params.get("model"), exc)
    return {}


async def apply_relation_fallback(
    graph: ExtractedGraph, settings: Settings, model_params: list[dict]
) -> ExtractedGraph:
    """Resolves whichever of `graph`'s relationships the deterministic tier couldn't place (see
    ExtractedRelationship.needs_relation_review), mutating them in place. Returns `graph` so
    callers can chain it directly onto merge_graph_chunks' result."""
    unresolved = [r for r in graph.relationships if r.needs_relation_review]
    if not unresolved:
        return graph

    phrases = sorted({r.relation_phrase or r.relation for r in unresolved})
    mapping = await _call_relation_resolver(phrases, settings, model_params)

    for rel in unresolved:
        phrase = rel.relation_phrase or rel.relation
        canonical = mapping.get(phrase)
        if canonical:
            logger.info("relation-resolve: %r -> %r (LLM fallback)", phrase, canonical)
            rel.relation = canonical
        else:
            logger.info(
                "relation-resolve: %r stayed on deterministic fallback %r", phrase, rel.relation
            )
        rel.needs_relation_review = False

    return graph
