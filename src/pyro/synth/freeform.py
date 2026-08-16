"""Freeform mode: no domain taxonomy — each extracted article is routed one at a time into an
existing or new topic doc. Run via the `synthesize` command/job stage, same as structured mode.

Unlike structured mode's deterministic domain-slug keys, freeform topic filenames are AI-chosen,
so routing decisions aren't naturally idempotent to replay. Rather than re-deciding every
article's topic on every run (expensive — O(n) LLM calls with a prompt that grows with every
existing doc — and non-deterministic, so replaying could relabel an already-settled topic under
a new slug), each article's routing decision is persisted once (Article.routed_doc_key) and a
run only processes articles that don't have one yet. To force a full rebuild (e.g. after
changing the routing prompt or freeform_route_source), delete the company's docs first — via the
dashboard's "delete all docs" or Database.delete_docs_for_company — which also clears every
article's routed_doc_key so the next run starts over. See run_freeform_synthesis."""

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
    """Fold company_name's not-yet-routed extracted articles into topic docs, one at a time
    (each routing decision depends on the docs built by prior ones in this run, so this runs
    sequentially, not concurrently). Already-routed articles (Article.routed_doc_key set) are
    left untouched — safe to call repeatedly as new articles get extracted, without re-deciding
    settled topics or duplicating material. Returns only the docs touched by this run, keyed by
    filename stem — untouched existing docs aren't included. Raises RuntimeError if
    company_name has no extracted articles at all; returns {} if all extracted articles are
    already routed (nothing new to do)."""
    all_articles = db.fetch_extracted(company_name)
    if not all_articles:
        raise RuntimeError(f"no extracted articles found for {company_name!r}")
    articles = [a for a in all_articles if not a.routed_doc_key]
    if settings.synthesis_article_limit is not None:
        articles = articles[: settings.synthesis_article_limit]
    if not articles:
        return {}

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
        db.mark_routed(article.id, stem)
        results[stem] = content
    return results
