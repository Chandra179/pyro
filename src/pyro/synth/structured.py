"""Structured mode: schema-validated extraction is already done (extract/pipeline.py); this
batches each domain's articles, summarizes, and merges into one architecture doc per domain
(plan.md 'Batching for Large-Scale Blogs')."""

from __future__ import annotations

import json
import logging

from litellm import acompletion

from pyro.config import Settings
from pyro.db import Article, Database
from pyro.router import call_with_rate_limit_retry, synthesis_model_params
from pyro.synth.common import (
    PromptPair,
    SynthesisContext,
    first_heading,
    slugify,
    strip_outer_markdown_fence,
)
from pyro.synth.prompts import (
    batch_synthesis_system_prompt,
    batch_synthesis_user_prompt,
    synthesis_system_prompt,
    synthesis_user_prompt,
)

logger = logging.getLogger(__name__)


def _batches(articles: list[Article], batch_size: int) -> list[list[Article]]:
    return [articles[i : i + batch_size] for i in range(0, len(articles), batch_size)]


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
    articles: list[Article], ctx: SynthesisContext, prompts: PromptPair
) -> list[dict]:
    facts_json = json.dumps([_article_summary(a) for a in articles], indent=2)
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {"role": "system", "content": prompts.system},
                {
                    "role": "user",
                    "content": prompts.user.format(
                        article_count=len(articles),
                        company_name=ctx.company_name,
                        facts_json_data=facts_json,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            **ctx.model_params,
        ),
        ctx.settings,
    )
    return json.loads(response.choices[0].message.content).get("summaries", [])


async def _final_synthesis(
    facts_data: list[dict],
    domain: str,
    total_articles: int,
    ctx: SynthesisContext,
    max_tokens: int,
    prompts: PromptPair,
) -> str:
    facts_json = json.dumps(facts_data, indent=2)
    model_params = {**ctx.model_params, "max_tokens": max_tokens}
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {"role": "system", "content": prompts.system},
                {
                    "role": "user",
                    "content": prompts.user.format(
                        total_articles=total_articles,
                        company_name=ctx.company_name,
                        domain=domain,
                        facts_json_data=facts_json,
                    ),
                },
            ],
            **model_params,
        ),
        ctx.settings,
    )
    return strip_outer_markdown_fence(response.choices[0].message.content)


async def _synthesize_domain(
    domain: str, articles: list[Article], ctx: SynthesisContext
) -> str:
    batches = _batches(articles, ctx.settings.synthesis_batch_size)
    if len(batches) == 1:
        facts_data = [_article_summary(a) for a in articles]
    else:
        batch_prompts = PromptPair(
            batch_synthesis_system_prompt(ctx.settings),
            batch_synthesis_user_prompt(ctx.settings),
        )
        facts_data = []
        for batch in batches:
            facts_data.extend(await _summarize_batch(batch, ctx, batch_prompts))

    prompts = PromptPair(
        synthesis_system_prompt(ctx.settings), synthesis_user_prompt(ctx.settings)
    )
    return await _final_synthesis(
        facts_data,
        domain,
        len(articles),
        ctx,
        ctx.settings.synthesis_max_tokens,
        prompts,
    )


async def run_structured_synthesis(
    db: Database, settings: Settings, company_name: str
) -> dict[str, str]:
    """Produce one architecture doc per domain for company_name, persist each as a doc in
    ArangoDB (key "architecture-<domain-slug>"), and return them keyed by domain name."""
    articles = db.fetch_extracted(company_name)
    if not articles:
        raise RuntimeError(f"no extracted articles found for {company_name!r}")
    if settings.synthesis_article_limit is not None:
        articles = articles[: settings.synthesis_article_limit]

    ctx = SynthesisContext(company_name, settings, synthesis_model_params(settings))
    groups = _group_by_domain(articles)

    results: dict[str, str] = {}
    for domain, domain_articles in groups.items():
        content = await _synthesize_domain(domain, domain_articles, ctx)
        results[domain] = content
        db.upsert_doc(
            f"architecture-{slugify(domain)}",
            company_name,
            content,
            heading=first_heading(content),
        )
    return results
