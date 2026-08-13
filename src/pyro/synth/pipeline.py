"""Per-domain batch synthesis + final merge pass (plan.md 'Batching for Large-Scale Blogs')."""

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


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "other"


# Some models wrap the whole markdown document in an outer ```markdown fence
# (as if it were a code block rather than the document itself), which breaks
# rendering (headers/Mermaid diagrams show as one inert code block instead).
_OUTER_FENCE_RE = re.compile(r"\A```(?:markdown|md)?\s*\n(.*)\n```\s*\Z", re.DOTALL)


def _strip_outer_markdown_fence(text: str) -> str:
    match = _OUTER_FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def _article_summary(article: Article) -> dict:
    facts = article.extracted_facts or {}
    return {
        "title": article.title or "",
        "topic": facts.get("topic", ""),
        "problem": facts.get("problem", ""),
        "solution": facts.get("solution", ""),
    }


def _group_by_domain(articles: list[Article]) -> dict[str, list[Article]]:
    groups: dict[str, list[Article]] = {}
    for article in articles:
        domain = (article.extracted_facts or {}).get("domain") or "Other"
        groups.setdefault(domain, []).append(article)
    return groups


async def _summarize_batch(
    articles: list[Article], company_name: str, model_params: dict
) -> list[dict]:
    facts_json = json.dumps([_article_summary(a) for a in articles], indent=2)
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
    return json.loads(response.choices[0].message.content).get("summaries", [])


async def _final_synthesis(
    facts_data: list[dict],
    company_name: str,
    domain: str,
    total_articles: int,
    model_params: dict,
    max_tokens: int,
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
                    domain=domain,
                    facts_json_data=facts_json,
                ),
            },
        ],
        max_tokens=max_tokens,
        **model_params,
    )
    return _strip_outer_markdown_fence(response.choices[0].message.content)


async def _synthesize_domain(
    domain: str, articles: list[Article], company_name: str, settings: Settings, model_params: dict
) -> str:
    batches = _batches(articles, settings.synthesis_batch_size)
    if len(batches) == 1:
        facts_data = [_article_summary(a) for a in articles]
    else:
        facts_data = []
        for batch in batches:
            facts_data.extend(await _summarize_batch(batch, company_name, model_params))

    return await _final_synthesis(
        facts_data, company_name, domain, len(articles), model_params, settings.synthesis_max_tokens
    )


async def run_synthesis(db: Database, settings: Settings, company_name: str) -> dict[str, str]:
    """Produce one architecture doc per domain for company_name, keyed by domain name."""
    articles = db.fetch_architectural(company_name)
    if not articles:
        raise RuntimeError(f"no architectural articles found for {company_name!r}")

    model_params = synthesis_model_params(settings)
    groups = _group_by_domain(articles)

    results: dict[str, str] = {}
    for domain, domain_articles in groups.items():
        results[domain] = await _synthesize_domain(
            domain, domain_articles, company_name, settings, model_params
        )
    return results
