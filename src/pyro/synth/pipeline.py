"""Per-domain batch synthesis + final merge pass (plan.md 'Batching for Large-Scale Blogs')."""

from __future__ import annotations

import json
import logging
import re

from litellm import acompletion

from pyro.config import Settings
from pyro.db import Article, Database
from pyro.router import call_with_rate_limit_retry, synthesis_model_params
from pyro.synth.prompts import (
    batch_synthesis_system_prompt,
    batch_synthesis_user_prompt,
    freeform_route_system_prompt,
    freeform_route_user_prompt,
    synthesis_system_prompt,
    synthesis_user_prompt,
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
    articles: list[Article],
    company_name: str,
    model_params: dict,
    system_prompt: str,
    user_template: str,
    settings: Settings,
) -> list[dict]:
    facts_json = json.dumps([_article_summary(a) for a in articles], indent=2)
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_template.format(
                        article_count=len(articles),
                        company_name=company_name,
                        facts_json_data=facts_json,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            **model_params,
        ),
        settings,
    )
    return json.loads(response.choices[0].message.content).get("summaries", [])


async def _final_synthesis(
    facts_data: list[dict],
    company_name: str,
    domain: str,
    total_articles: int,
    model_params: dict,
    max_tokens: int,
    system_prompt: str,
    user_template: str,
    settings: Settings,
) -> str:
    facts_json = json.dumps(facts_data, indent=2)
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_template.format(
                        total_articles=total_articles,
                        company_name=company_name,
                        domain=domain,
                        facts_json_data=facts_json,
                    ),
                },
            ],
            max_tokens=max_tokens,
            **model_params,
        ),
        settings,
    )
    return _strip_outer_markdown_fence(response.choices[0].message.content)


async def _synthesize_domain(
    domain: str, articles: list[Article], company_name: str, settings: Settings, model_params: dict
) -> str:
    batches = _batches(articles, settings.synthesis_batch_size)
    if len(batches) == 1:
        facts_data = [_article_summary(a) for a in articles]
    else:
        batch_system_prompt = batch_synthesis_system_prompt(settings)
        batch_user_template = batch_synthesis_user_prompt(settings)
        facts_data = []
        for batch in batches:
            facts_data.extend(
                await _summarize_batch(
                    batch, company_name, model_params, batch_system_prompt, batch_user_template, settings
                )
            )

    return await _final_synthesis(
        facts_data,
        company_name,
        domain,
        len(articles),
        model_params,
        settings.synthesis_max_tokens,
        synthesis_system_prompt(settings),
        synthesis_user_prompt(settings),
        settings,
    )


def build_docs_index(db: Database, company_name: str) -> str:
    """One line per existing synthesized doc: key + heading, as routing context for the AI."""
    docs = db.list_docs(company_name)
    if not docs:
        return "(none yet — this will be the first file)"
    return "\n".join(f"- {d['_key']}.md: {d.get('heading') or d['_key']}" for d in docs)


def _first_heading(content: str) -> str:
    return next(
        (line.lstrip("#").strip() for line in content.splitlines() if line.startswith("#")),
        "",
    )


def _parse_routed_response(raw: str) -> tuple[str, str]:
    """Parse the "FILENAME: x.md\n---\n<doc>" format the freeform_route prompt returns."""
    lines = raw.strip().splitlines()
    if not lines or not lines[0].strip().upper().startswith("FILENAME:"):
        raise ValueError(f"routed response missing FILENAME header: {raw[:200]!r}")
    filename = lines[0].split(":", 1)[1].strip()
    try:
        sep_idx = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError(f"routed response missing '---' separator: {raw[:200]!r}") from None
    content = "\n".join(lines[sep_idx + 1 :]).strip()
    if not content:
        raise ValueError("routed response had an empty document body")
    return filename, content


async def route_and_update_doc(
    existing_files_index: str,
    title: str,
    extraction_text: str,
    company_name: str,
    settings: Settings,
    model_params: dict,
) -> tuple[str, str]:
    """Freeform mode: decide which topic file this article belongs to (or that it needs a new
    one), and fold it in. Returns (filename, full updated document)."""
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {"role": "system", "content": freeform_route_system_prompt(settings)},
                {
                    "role": "user",
                    "content": freeform_route_user_prompt(settings).format(
                        company_name=company_name,
                        existing_files_index=existing_files_index,
                        title=title,
                        extraction_text=extraction_text,
                    ),
                },
            ],
            max_tokens=settings.synthesis_max_tokens,
            **model_params,
        ),
        settings,
    )
    filename, content = _parse_routed_response(response.choices[0].message.content)
    stem = slugify(filename.removesuffix(".md").removesuffix(".markdown"))
    return f"{stem}.md", _strip_outer_markdown_fence(content)


async def run_synthesis(db: Database, settings: Settings, company_name: str) -> dict[str, str]:
    """Produce one architecture doc per domain for company_name, persist each as a doc in
    ArangoDB (key "architecture-<domain-slug>"), and return them keyed by domain name."""
    articles = db.fetch_extracted(company_name)
    if not articles:
        raise RuntimeError(f"no extracted articles found for {company_name!r}")
    if settings.synthesis_article_limit is not None:
        articles = articles[: settings.synthesis_article_limit]

    model_params = synthesis_model_params(settings)
    groups = _group_by_domain(articles)

    results: dict[str, str] = {}
    for domain, domain_articles in groups.items():
        content = await _synthesize_domain(
            domain, domain_articles, company_name, settings, model_params
        )
        results[domain] = content
        db.upsert_doc(f"architecture-{slugify(domain)}", company_name, content, heading=_first_heading(content))
    return results
