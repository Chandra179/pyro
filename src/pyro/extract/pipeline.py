"""Bounded async extraction pipeline with per-model schema-validation retry.

litellm's Router only advances within/across the cascade on raised exceptions (429/503/timeout,
handled via its own routing_strategy/allowed_fails/cooldown_time/fallbacks — see
router/cascade.py) — a 200 OK with malformed/schema-invalid JSON isn't an exception, so it needs
its own retry loop on top, bounded by router.cascade_parse_retry_attempts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from json_repair import repair_json
from litellm import Router

from pyro.clean.chunk import chunk_text
from pyro.config import Settings
from pyro.db import Database
from pyro.extract.prompts import extraction_system_prompt, extraction_user_prompt
from pyro.extract.relation_resolve import apply_relation_fallback
from pyro.extract.schema import (
    DOMAINS,
    RELATION_KINDS,
    ExtractedGraph,
    merge_graph_chunks,
)
from pyro.router import build_router, call_opencode_go_direct, cascade_entrypoint

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _decoding_params(settings: Settings) -> dict:
    """Shared temperature/frequency_penalty for extraction calls (see Settings docstring).
    max_tokens excluded — it's tier-specific, from each model's own `params` below."""
    return {
        "temperature": settings.extraction_temperature,
        "frequency_penalty": settings.extraction_frequency_penalty,
    }


def _build_router_or_none(settings: Settings) -> Router | None:
    """None means no litellm-routable tier is configured — extraction still proceeds if the
    OpenCode Go direct-call bypass is enabled (see _run_model_cascade)."""
    try:
        return build_router(settings)
    except RuntimeError:
        return None


async def _run_model_cascade(
    messages: list[dict],
    router: Router | None,
    parse_response: Callable[[str], T],
    decoding_params: dict | None,
    settings: Settings,
    extra_kwargs: dict | None = None,
) -> T:
    """Call through the litellm Router (paid tier, falling back to free tier — see
    router/cascade.py), retrying on a raised provider error (Router's own job) or on
    parse_response rejecting the content (module docstring). Once the Router is exhausted, falls
    to OpenCode Go's direct-call bypass if enabled."""
    last_error: Exception | None = None
    entrypoint = cascade_entrypoint(settings)
    if router is not None and entrypoint is not None:
        for _ in range(settings.router.cascade_parse_retry_attempts):
            try:
                response = await router.acompletion(
                    model=entrypoint,
                    messages=messages,
                    **(decoding_params or {}),
                    **(extra_kwargs or {}),
                )
                return parse_response(response.choices[0].message.content)
            except Exception as exc:
                logger.warning("cascade call failed: %s", exc)
                last_error = exc

    if settings.router.opencode_go_enabled and settings.opencode_api_key:
        try:
            content = await call_opencode_go_direct(
                messages,
                settings,
                max_tokens=settings.extraction_max_tokens,
                decoding_params=decoding_params,
                response_format=(extra_kwargs or {}).get("response_format"),
            )
            return parse_response(content)
        except Exception as exc:
            logger.warning("opencode go (direct) failed: %s", exc)
            last_error = exc

    raise RuntimeError(f"all models in cascade failed: {last_error}") from last_error


def _parse_extracted_graph(raw: str) -> ExtractedGraph:
    parsed = repair_json(raw, return_objects=True)
    return ExtractedGraph.model_validate(parsed)


@dataclass(frozen=True)
class ExtractionRunConfig:
    """Fixed for one `extract_article` call, shared across all its chunks."""

    router: Router | None
    system_prompt: str
    user_template: str
    settings: Settings
    domains: list[str] = field(default_factory=lambda: DOMAINS)
    decoding_params: dict | None = None


async def extract_chunk(
    title: str,
    url: str,
    content: str,
    config: ExtractionRunConfig,
) -> ExtractedGraph:
    messages = [
        {"role": "system", "content": config.system_prompt},
        {
            "role": "user",
            "content": config.user_template.format(
                title=title,
                url=url,
                content=content,
                domains=", ".join(config.domains),
                relations="\n".join(f"- {r}" for r in RELATION_KINDS),
            ),
        },
    ]
    return await _run_model_cascade(
        messages,
        config.router,
        _parse_extracted_graph,
        config.decoding_params,
        config.settings,
        extra_kwargs={"response_format": {"type": "json_object"}},
    )


async def extract_article(
    title: str,
    url: str,
    cleaned_text: str,
    settings: Settings,
    router: Router | None = None,
) -> ExtractedGraph:
    """router defaults to rebuilding the cascade from settings; callers processing many articles
    in one run should build it once (via `pyro.router.build_router`, or None if nothing's
    configured) and pass it in instead — see run_extraction."""
    if router is None:
        router = _build_router_or_none(settings)
    system_prompt = extraction_system_prompt(settings)
    user_template = extraction_user_prompt(settings)
    chunks = chunk_text(
        cleaned_text,
        token_threshold=settings.chunk_token_threshold,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    config = ExtractionRunConfig(
        router=router,
        system_prompt=system_prompt,
        user_template=user_template,
        settings=settings,
        domains=settings.domains,
        decoding_params=_decoding_params(settings),
    )
    graphs = [await extract_chunk(title, url, chunk, config) for chunk in chunks]
    merged = merge_graph_chunks(graphs)
    return await apply_relation_fallback(merged, settings, router)


async def run_extraction(
    db: Database,
    settings: Settings,
    limit: int | None = None,
    company_name: str | None = None,
) -> int:
    """Extract the entity/relationship graph for unextracted, cleaned articles, optionally scoped
    to one company. Returns count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit, company_name=company_name)
    if not articles:
        return 0

    sem = asyncio.Semaphore(settings.extraction_concurrency)
    router = _build_router_or_none(settings)

    async def _process(article) -> None:
        async with sem:
            try:
                graph = await extract_article(
                    article.title or "",
                    article.source_url,
                    article.cleaned_text,
                    settings,
                    router,
                )
            except Exception:
                logger.exception("extraction failed for %s", article.id)
                return
            db.mark_extracted(article.id, graph.model_dump())

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
