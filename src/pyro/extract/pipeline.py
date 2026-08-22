"""Bounded async extraction pipeline with per-model schema-validation retry.

LiteLLM Router only advances cascade tiers on raised exceptions (429/503/timeout) — a 200 OK
with malformed/schema-invalid JSON needs its own advance-to-next-model loop, so we iterate the
concrete model list directly instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from json_repair import repair_json
from litellm import acompletion

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
from pyro.router import call_with_rate_limit_retry, concrete_model_params

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _decoding_params(settings: Settings) -> dict:
    """Shared temperature/frequency_penalty for extraction calls (see Settings docstring).
    max_tokens excluded — it's tier-specific, from each model's own `params` below."""
    return {
        "temperature": settings.extraction_temperature,
        "frequency_penalty": settings.extraction_frequency_penalty,
    }


async def _run_model_cascade(
    messages: list[dict],
    model_params: list[dict],
    parse_response: Callable[[str], T],
    decoding_params: dict | None,
    settings: Settings,
    extra_kwargs: dict | None = None,
) -> T:
    """Call through the concrete model list, advancing to the next model on a raised provider
    error or on parse_response rejecting the content (see module docstring)."""
    last_error: Exception | None = None
    for params in model_params:
        try:
            response = await call_with_rate_limit_retry(
                lambda params=params: acompletion(
                    messages=messages,
                    **(decoding_params or {}),
                    **(extra_kwargs or {}),
                    **params,
                ),
                settings,
            )
            return parse_response(response.choices[0].message.content)
        except Exception as exc:
            logger.warning("model %s failed: %s", params["model"], exc)
            last_error = exc

    raise RuntimeError(f"all models in cascade failed: {last_error}") from last_error


def _parse_extracted_graph(raw: str) -> ExtractedGraph:
    parsed = repair_json(raw, return_objects=True)
    return ExtractedGraph.model_validate(parsed)


@dataclass(frozen=True)
class ExtractionRunConfig:
    """Fixed for one `extract_article` call, shared across all its chunks."""

    model_params: list[dict]
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
        config.model_params,
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
    model_params: list[dict] | None = None,
) -> ExtractedGraph:
    """model_params defaults to rebuilding the cascade from settings; callers processing many
    articles in one run should build it once and pass it in instead."""
    model_params = model_params if model_params is not None else concrete_model_params(settings)
    system_prompt = extraction_system_prompt(settings)
    user_template = extraction_user_prompt(settings)
    chunks = chunk_text(
        cleaned_text,
        token_threshold=settings.chunk_token_threshold,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    config = ExtractionRunConfig(
        model_params=model_params,
        system_prompt=system_prompt,
        user_template=user_template,
        settings=settings,
        domains=settings.domains,
        decoding_params=_decoding_params(settings),
    )
    graphs = [await extract_chunk(title, url, chunk, config) for chunk in chunks]
    merged = merge_graph_chunks(graphs)
    return await apply_relation_fallback(merged, settings, model_params)


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
    model_params = concrete_model_params(settings)

    async def _process(article) -> None:
        async with sem:
            try:
                graph = await extract_article(
                    article.title or "",
                    article.source_url,
                    article.cleaned_text,
                    settings,
                    model_params,
                )
            except Exception:
                logger.exception("extraction failed for %s", article.id)
                return
            db.mark_extracted(article.id, graph.model_dump())

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
