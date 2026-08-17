"""Bounded async extraction pipeline with per-model schema-validation retry.

Plan.md 'Validation Layer': the LiteLLM Router only advances tiers on raised
exceptions (429/503/timeout). A 200 OK response with malformed/schema-invalid
JSON needs its own advance-to-next-model loop, so we iterate the concrete
model list directly rather than relying on Router's internal fallback state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

from json_repair import repair_json
from litellm import acompletion

from pyro.clean.chunk import chunk_text
from pyro.config import Settings
from pyro.db import Database
from pyro.extract.prompts import extraction_system_prompt, extraction_user_prompt
from pyro.extract.schema import DOMAINS, ExtractedGraph, merge_graph_chunks
from pyro.router import call_with_rate_limit_retry, concrete_model_params

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _decoding_params(settings: Settings) -> dict:
    """Shared temperature/frequency_penalty for extraction calls — see
    Settings.extraction_temperature docstring for why these matter against free-tier models.
    max_tokens is deliberately not here: it's tier-specific (see router._max_tokens_for) and
    comes from each model's own `params` in extract_chunk below."""
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
    """Call through the concrete model list, applying parse_response to each response and
    advancing to the next model on any failure — a raised provider error (429/503/timeout/etc.)
    or parse_response rejecting the content (schema-invalid JSON, degenerate output, ...). The
    Router's own fallback only advances tiers on raised exceptions, so a 200 OK response that
    parse_response rejects needs this loop rather than Router's internal fallback state."""
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


async def extract_chunk(
    title: str,
    url: str,
    content: str,
    model_params: list[dict],
    system_prompt: str,
    user_template: str,
    domains: list[str] = DOMAINS,
    decoding_params: dict | None = None,
    settings: Settings | None = None,
) -> ExtractedGraph:
    settings = settings or Settings()
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": user_template.format(
                title=title, url=url, content=content, domains=", ".join(domains)
            ),
        },
    ]
    return await _run_model_cascade(
        messages,
        model_params,
        _parse_extracted_graph,
        decoding_params,
        settings,
        extra_kwargs={"response_format": {"type": "json_object"}},
    )


async def extract_article(
    title: str,
    url: str,
    cleaned_text: str,
    settings: Settings,
    model_params: list[dict] | None = None,
) -> ExtractedGraph:
    """model_params defaults to rebuilding the cascade from settings, but callers processing
    many articles from the same run (see run_extraction) should build it once and pass it in —
    settings don't change mid-run, so recomputing per article is redundant."""
    model_params = model_params if model_params is not None else concrete_model_params(settings)
    system_prompt = extraction_system_prompt(settings)
    user_template = extraction_user_prompt(settings)
    chunks = chunk_text(
        cleaned_text,
        token_threshold=settings.chunk_token_threshold,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    decoding_params = _decoding_params(settings)
    graphs = [
        await extract_chunk(
            title,
            url,
            chunk,
            model_params,
            system_prompt,
            user_template,
            settings.domains,
            decoding_params,
            settings,
        )
        for chunk in chunks
    ]
    return merge_graph_chunks(graphs)


async def run_extraction(
    db: Database, settings: Settings, limit: int | None = None
) -> int:
    """Extract the entity/relationship graph for all unextracted, cleaned articles. Returns
    count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit)
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
