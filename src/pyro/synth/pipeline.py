"""Batch synthesis + final merge pass (plan.md 'Batching for Large-Scale Blogs')."""

from __future__ import annotations

import json
import logging
import re

from litellm import acompletion

from pyro.config import Settings
from pyro.db import Article, Database
from pyro.router import synthesis_model_params
from pyro.synth.prompts import (
    BATCH_SYNTHESIS_SYSTEM_PROMPT,
    BATCH_SYNTHESIS_USER_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_PROMPT,
)

logger = logging.getLogger(__name__)


def _batches(articles: list[Article], batch_size: int) -> list[list[Article]]:
    return [articles[i : i + batch_size] for i in range(0, len(articles), batch_size)]


# Some models wrap the whole markdown document in an outer ```markdown fence
# (as if it were a code block rather than the document itself), which breaks
# rendering (headers/Mermaid diagrams show as one inert code block instead).
_OUTER_FENCE_RE = re.compile(r"\A```(?:markdown|md)?\s*\n(.*)\n```\s*\Z", re.DOTALL)


def _strip_outer_markdown_fence(text: str) -> str:
    match = _OUTER_FENCE_RE.match(text.strip())
    return match.group(1) if match else text


async def _summarize_batch(
    articles: list[Article], company_name: str, model_params: dict
) -> dict:
    facts_json = json.dumps([a.extracted_facts for a in articles], indent=2)
    response = await acompletion(
        messages=[
            {"role": "system", "content": BATCH_SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": BATCH_SYNTHESIS_USER_PROMPT.format(
                    article_count=len(articles),
                    company_name=company_name,
                    facts_json_data=facts_json,
                ),
            },
        ],
        response_format={"type": "json_object"},
        **model_params,
    )
    return json.loads(response.choices[0].message.content)


async def _final_synthesis(
    facts_data: list[dict], company_name: str, total_articles: int, model_params: dict
) -> str:
    facts_json = json.dumps(facts_data, indent=2)
    response = await acompletion(
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": SYNTHESIS_USER_PROMPT.format(
                    total_articles=total_articles,
                    company_name=company_name,
                    facts_json_data=facts_json,
                ),
            },
        ],
        **model_params,
    )
    return _strip_outer_markdown_fence(response.choices[0].message.content)


async def run_synthesis(db: Database, settings: Settings, company_name: str) -> str:
    """Produce the final architecture.md content string for company_name."""
    articles = db.fetch_architectural(company_name)
    if not articles:
        raise RuntimeError(f"no architectural articles found for {company_name!r}")

    model_params = synthesis_model_params(settings)
    batches = _batches(articles, settings.synthesis_batch_size)

    if len(batches) == 1:
        facts_data = [a.extracted_facts for a in articles]
    else:
        facts_data = [
            await _summarize_batch(batch, company_name, model_params) for batch in batches
        ]

    return await _final_synthesis(facts_data, company_name, len(articles), model_params)
