"""LLM fallback tier for relation-predicate canonicalization.

Mirrors graph/resolve.py's two-tier shape for entity names: a free deterministic pass runs first
(extract/schema.py's normalize_relation), and only phrases it couldn't place reach this module's
single batched LLM call, run after an article's chunks are merged rather than per-edge inside the
validator — keeps schema validation synchronous, and LLM cost to one call per article.
"""

from __future__ import annotations

import json
import logging

from json_repair import repair_json
from litellm import Router
from pydantic import BaseModel, ValidationError

from pyro.config import Settings
from pyro.extract.prompts import (
    relation_resolve_system_prompt,
    relation_resolve_user_prompt,
)
from pyro.extract.schema import RELATION_KINDS, ExtractedGraph
from pyro.router import call_opencode_go_direct, cascade_entrypoint

logger = logging.getLogger(__name__)


class _ResolvedPhrase(BaseModel):
    phrase: str
    canonical: str | None = None


class _RelationResolutionResponse(BaseModel):
    resolved: list[_ResolvedPhrase] = []


async def _call_relation_resolver(
    phrases: list[str], settings: Settings, router: Router | None
) -> dict[str, str]:
    """One cascade-backed call mapping as many of `phrases` onto RELATION_KINDS as the model is
    confident about. A phrase absent from the returned mapping (model said null, or every tier
    failed) is left for the caller's existing deterministic fallback."""
    system = relation_resolve_system_prompt(settings)
    user = relation_resolve_user_prompt(settings).format(
        relations="\n".join(f"- {r}" for r in RELATION_KINDS),
        phrases_json=json.dumps(phrases, indent=2),
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    entrypoint = cascade_entrypoint(settings)
    if router is not None and entrypoint is not None:
        for _ in range(settings.router.cascade_parse_retry_attempts):
            try:
                response = await router.acompletion(
                    model=entrypoint,
                    messages=messages,
                    response_format={"type": "json_object"},
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
                logger.warning("relation-resolve cascade call failed: %s", exc)

    if settings.router.opencode_go_enabled and settings.opencode_api_key:
        try:
            content = await call_opencode_go_direct(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                settings,
                max_tokens=settings.extraction_max_tokens,
                response_format={"type": "json_object"},
            )
            parsed = _RelationResolutionResponse.model_validate(
                repair_json(content, return_objects=True)
            )
            return {
                item.phrase: item.canonical
                for item in parsed.resolved
                if item.canonical in RELATION_KINDS
            }
        except (Exception, ValidationError) as exc:
            logger.warning("relation-resolve opencode go (direct) failed: %s", exc)

    return {}


async def apply_relation_fallback(
    graph: ExtractedGraph, settings: Settings, router: Router | None
) -> ExtractedGraph:
    """Resolves whichever of `graph`'s relationships the deterministic tier couldn't place (see
    ExtractedRelationship.needs_relation_review), mutating them in place. Returns `graph` so
    callers can chain it directly onto merge_graph_chunks' result."""
    unresolved = [r for r in graph.relationships if r.needs_relation_review]
    if not unresolved:
        return graph

    phrases = sorted({r.relation_phrase or r.relation for r in unresolved})
    mapping = await _call_relation_resolver(phrases, settings, router)

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
