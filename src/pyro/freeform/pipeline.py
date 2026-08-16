"""Freeform mode: plain-text extraction, each article immediately routed into a topic doc in
ArangoDB (existing doc updated, or a new one created) instead of a separate batch synthesis
pass."""

from __future__ import annotations

import asyncio
import logging

from pyro.config import Settings
from pyro.db import Database
from pyro.extract.pipeline import extract_article_freeform
from pyro.router import synthesis_model_params
from pyro.synth.pipeline import _first_heading, build_docs_index, route_and_update_doc

logger = logging.getLogger(__name__)


async def run_freeform_extraction(
    db: Database, settings: Settings, company_name: str, limit: int | None = None
) -> int:
    """Extract each unprocessed article, then route it: fold it into an existing topic doc in
    ArangoDB or create a new one. Returns count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit)
    if not articles:
        return 0

    sem = asyncio.Semaphore(settings.extraction_concurrency)
    # Extraction runs concurrently; the docs-index scan + routing decision + write is a critical
    # section serialized by this lock so concurrent articles don't race on the same doc or both
    # decide to create a new doc for what should be one topic.
    doc_lock = asyncio.Lock()
    synth_params = synthesis_model_params(settings)

    async def _process(article) -> None:
        async with sem:
            try:
                summary = await extract_article_freeform(
                    article.title or "", article.source_url, article.cleaned_text, settings
                )
            except Exception:
                logger.exception("freeform extraction failed for %s", article.id)
                return
            db.mark_extracted(article.id, {"summary": summary})

        route_text = article.cleaned_text if settings.freeform_route_source == "cleaned_text" else summary

        try:
            async with doc_lock:
                docs_index = build_docs_index(db, company_name)
                key, content = await route_and_update_doc(
                    docs_index,
                    article.title or article.source_url,
                    route_text,
                    company_name,
                    settings,
                    synth_params,
                )
                db.upsert_doc(key.removesuffix(".md"), company_name, content, heading=_first_heading(content))
        except Exception:
            # Article stays marked extracted (its summary is saved) even if routing fails —
            # a later run can't retry it automatically, but nothing is lost, and one bad
            # article's routing failure must not take down every other article's gather().
            logger.exception("freeform routing failed for %s", article.id)

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
