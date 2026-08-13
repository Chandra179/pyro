"""Bounded async extraction pipeline with per-model schema-validation retry.

Plan.md 'Validation Layer': the LiteLLM Router only advances tiers on raised
exceptions (429/503/timeout). A 200 OK response with malformed/schema-invalid
JSON needs its own advance-to-next-model loop, so we iterate the concrete
model list directly rather than relying on Router's internal fallback state.
"""

from __future__ import annotations

import asyncio
import logging

from json_repair import repair_json
from litellm import acompletion
from pydantic import ValidationError

from pyro.clean.chunk import chunk_text
from pyro.config import Settings
from pyro.db import Database
from pyro.extract.prompts import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_USER_PROMPT
from pyro.extract.schema import DOMAINS, ExtractedFacts, merge_facts
from pyro.router import concrete_model_params

logger = logging.getLogger(__name__)


async def extract_chunk(
    title: str, url: str, content: str, model_params: list[dict], domains: list[str] = DOMAINS
) -> ExtractedFacts:
    """Call through the concrete model list, validating each response against the
    Pydantic schema and advancing to the next model on any failure."""
    last_error: Exception | None = None
    for params in model_params:
        try:
            response = await acompletion(
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": EXTRACTION_USER_PROMPT.format(
                            title=title, url=url, content=content, domains=", ".join(domains)
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                **params,
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
    chunks = chunk_text(
        cleaned_text,
        token_threshold=settings.chunk_token_threshold,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    facts_list = [
        await extract_chunk(title, url, chunk, model_params, settings.domains) for chunk in chunks
    ]
    return merge_facts(facts_list, settings.domains)


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
            db.mark_extracted(article.id, facts.is_architectural, facts.model_dump())

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
