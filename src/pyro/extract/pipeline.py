"""Bounded async extraction pipeline with per-model schema-validation retry.

Plan.md 'Validation Layer': the LiteLLM Router only advances tiers on raised
exceptions (429/503/timeout). A 200 OK response with malformed/schema-invalid
JSON needs its own advance-to-next-model loop, so we iterate the concrete
model list directly rather than relying on Router's internal fallback state.
"""

from __future__ import annotations

import asyncio
import logging
import re

from json_repair import repair_json
from litellm import acompletion
from pydantic import ValidationError

from pyro.clean.chunk import chunk_text
from pyro.config import Settings
from pyro.db import Database
from pyro.extract.prompts import (
    extraction_freeform_system_prompt,
    extraction_freeform_user_prompt,
    extraction_system_prompt,
    extraction_user_prompt,
)
from pyro.extract.schema import DOMAINS, ExtractedFacts, merge_facts
from pyro.router import call_with_rate_limit_retry, concrete_model_params

logger = logging.getLogger(__name__)


def _decoding_params(settings: Settings) -> dict:
    """Shared temperature/frequency_penalty/max_tokens for extraction calls — see
    Settings.extraction_temperature docstring for why these matter against free-tier models."""
    return {
        "temperature": settings.extraction_temperature,
        "frequency_penalty": settings.extraction_frequency_penalty,
        "max_tokens": settings.extraction_max_tokens,
    }


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
) -> ExtractedFacts:
    """Call through the concrete model list, validating each response against the
    Pydantic schema and advancing to the next model on any failure."""
    settings = settings or Settings()
    last_error: Exception | None = None
    for params in model_params:
        try:
            response = await call_with_rate_limit_retry(
                lambda params=params: acompletion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": user_template.format(
                                title=title, url=url, content=content, domains=", ".join(domains)
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                    **(decoding_params or {}),
                    **params,
                ),
                settings,
            )
            raw = response.choices[0].message.content
            parsed = repair_json(raw, return_objects=True)
            return ExtractedFacts.model_validate(parsed)
        except ValidationError as exc:
            logger.warning("model %s returned invalid JSON/schema: %s", params["model"], exc)
            last_error = exc
        except Exception as exc:  # provider-level error (429/503/timeout/etc.)
            logger.warning("model %s failed: %s", params["model"], exc)
            last_error = exc

    raise RuntimeError(f"all models in cascade failed: {last_error}") from last_error


async def extract_article(
    title: str, url: str, cleaned_text: str, settings: Settings
) -> ExtractedFacts:
    model_params = concrete_model_params(settings)
    system_prompt = extraction_system_prompt(settings)
    user_template = extraction_user_prompt(settings)
    chunks = chunk_text(
        cleaned_text,
        token_threshold=settings.chunk_token_threshold,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    decoding_params = _decoding_params(settings)
    facts_list = [
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
    return merge_facts(facts_list, settings.domains)


_DEGENERATE_REPEAT_RE = re.compile(r"\b(\w+)\b(?:\s+\1\b){4,}", re.IGNORECASE)


def _is_degenerate(text: str) -> bool:
    """True if some word repeats 5+ times in a row — the decoding-collapse failure mode some
    free/small models fall into near their output limit (e.g. "Lorem Lorem Lorem ..."). A 200 OK
    response like this passes schema/exception checks but is garbage, so it needs its own check
    to trigger the same advance-to-next-model behavior as a provider error."""
    return bool(_DEGENERATE_REPEAT_RE.search(text))


async def extract_freeform_chunk(
    title: str,
    url: str,
    content: str,
    model_params: list[dict],
    system_prompt: str,
    user_template: str,
    decoding_params: dict | None = None,
    settings: Settings | None = None,
) -> str:
    """Same model-cascade fallback as extract_chunk, but no schema — returns raw text."""
    settings = settings or Settings()
    last_error: Exception | None = None
    for params in model_params:
        try:
            response = await call_with_rate_limit_retry(
                lambda params=params: acompletion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_template.format(title=title, url=url, content=content)},
                    ],
                    **(decoding_params or {}),
                    **params,
                ),
                settings,
            )
            text = response.choices[0].message.content
            if _is_degenerate(text):
                raise ValueError("degenerate output (repeated-word collapse)")
            return text
        except Exception as exc:  # provider-level error (429/503/timeout/etc.) or degenerate output
            logger.warning("model %s failed: %s", params["model"], exc)
            last_error = exc

    raise RuntimeError(f"all models in cascade failed: {last_error}") from last_error


async def extract_article_freeform(title: str, url: str, cleaned_text: str, settings: Settings) -> str:
    """Freeform mode: one plain-text summary per article, no chunking/merging."""
    model_params = concrete_model_params(settings)
    system_prompt = extraction_freeform_system_prompt(settings)
    user_template = extraction_freeform_user_prompt(settings)
    return await extract_freeform_chunk(
        title, url, cleaned_text, model_params, system_prompt, user_template, _decoding_params(settings), settings
    )


async def run_extraction(db: Database, settings: Settings, limit: int | None = None) -> int:
    """Extract facts for all unextracted, cleaned articles. Returns count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit)
    if not articles:
        return 0

    sem = asyncio.Semaphore(settings.extraction_concurrency)

    async def _process(article) -> None:
        async with sem:
            try:
                facts = await extract_article(
                    article.title or "", article.source_url, article.cleaned_text, settings
                )
            except Exception:
                logger.exception("extraction failed for %s", article.id)
                return
            db.mark_extracted(article.id, facts.model_dump())

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
