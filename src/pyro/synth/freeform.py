"""Freeform mode: no domain taxonomy — each extracted article is routed one at a time into an
existing or new topic doc. Run via the `synthesize` command/job stage, same as structured mode;
unlike structured mode's deterministic domain-slug keys, freeform topic filenames are AI-chosen
and depend on processing order, so a re-run rebuilds every doc from scratch rather than upserting
in place (see run_freeform_synthesis)."""

from __future__ import annotations

import json
import logging

from litellm import acompletion

from pyro.config import Settings
from pyro.db import Database
from pyro.router import call_with_rate_limit_retry, synthesis_model_params
from pyro.synth.common import (
    SynthesisContext,
    first_heading,
    slugify,
    strip_outer_markdown_fence,
)
from pyro.synth.prompts import freeform_route_system_prompt, freeform_route_user_prompt

logger = logging.getLogger(__name__)


def _existing_docs_context(db: Database, company_name: str) -> str:
    """Full current content of every topic doc so far, for the routing prompt to merge into —
    not just filenames/headings, otherwise the model has nothing to actually merge with and
    silently discards prior content when it reuses an existing file."""
    docs = db.list_docs(company_name)
    if not docs:
        return "(none yet — this will be the first file)"
    return "\n\n".join(
        f"### {d['_key']}.md — {d.get('heading') or d['_key']}\n\n{d['content']}"
        for d in docs
    )


def _parse_routed_response(raw: str) -> tuple[str, str]:
    """Parse the {"filename": ..., "content": ...} JSON object the freeform_route
    prompt returns (response_format=json_object)."""
    parsed = json.loads(raw)
    filename = (parsed.get("filename") or "").strip()
    content = (parsed.get("content") or "").strip()
    if not filename:
        raise ValueError(f"routed response missing 'filename': {raw[:200]!r}")
    if not content:
        raise ValueError(f"routed response missing 'content': {raw[:200]!r}")
    return filename, content


async def route_and_update_doc(
    existing_docs_context: str,
    title: str,
    extraction_text: str,
    ctx: SynthesisContext,
) -> tuple[str, str]:
    """Decide which topic file this article belongs to (or that it needs a new one), and fold
    it in. Returns (filename, full updated document)."""
    model_params = {**ctx.model_params, "max_tokens": ctx.settings.synthesis_max_tokens}
    response = await call_with_rate_limit_retry(
        lambda: acompletion(
            messages=[
                {
                    "role": "system",
                    "content": freeform_route_system_prompt(ctx.settings),
                },
                {
                    "role": "user",
                    "content": freeform_route_user_prompt(ctx.settings).format(
                        company_name=ctx.company_name,
                        existing_files_index=existing_docs_context,
                        title=title,
                        extraction_text=extraction_text,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            **model_params,
        ),
        ctx.settings,
    )
    filename, content = _parse_routed_response(response.choices[0].message.content)
    stem = slugify(filename.removesuffix(".md").removesuffix(".markdown"))
    return f"{stem}.md", strip_outer_markdown_fence(content)


def _route_text(article, settings: Settings) -> str:
    if settings.freeform_route_source == "cleaned_text":
        return article.cleaned_text
    return (article.extracted_facts or {}).get("summary", "")


async def run_freeform_synthesis(
    db: Database, settings: Settings, company_name: str
) -> dict[str, str]:
    """Rebuild every topic doc for company_name from its already-extracted articles, folding
    them in one at a time (each routing decision depends on the docs built by prior ones, so
    this runs sequentially, not concurrently). Existing docs are cleared first: freeform
    filenames are AI-chosen per run rather than deterministic slugs, so replaying on top of
    stale docs would duplicate material instead of cleanly regenerating it — this is what makes
    the command safely re-runnable after changing freeform_route_source or the routing prompt.
    """
    articles = db.fetch_extracted(company_name)
    if not articles:
        raise RuntimeError(f"no extracted articles found for {company_name!r}")
    if settings.synthesis_article_limit is not None:
        articles = articles[: settings.synthesis_article_limit]

    db.delete_docs_for_company(company_name)
    ctx = SynthesisContext(company_name, settings, synthesis_model_params(settings))

    results: dict[str, str] = {}
    for article in articles:
        existing_docs = _existing_docs_context(db, company_name)
        key, content = await route_and_update_doc(
            existing_docs,
            article.title or article.source_url,
            _route_text(article, settings),
            ctx,
        )
        stem = key.removesuffix(".md")
        db.upsert_doc(stem, company_name, content, heading=first_heading(content))
        results[stem] = content
    return results
