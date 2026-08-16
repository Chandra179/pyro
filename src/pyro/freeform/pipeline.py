"""Freeform mode extraction: plain-text extraction for each unprocessed article. Routing each
extracted article into a topic doc is a separate step — see synth/freeform.py's
run_freeform_synthesis, run via the `synthesize` command/job stage — so that changing
freeform_route_source or the routing prompt can be replayed without re-running (paid) extraction
calls."""

from __future__ import annotations

import asyncio
import logging

from pyro.config import Settings
from pyro.db import Database
from pyro.extract.pipeline import extract_article_freeform

logger = logging.getLogger(__name__)


async def run_freeform_extraction(
    db: Database, settings: Settings, limit: int | None = None
) -> int:
    """Extract each unprocessed article's freeform summary and mark it extracted. Returns
    count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit)
    if not articles:
        return 0

    sem = asyncio.Semaphore(settings.extraction_concurrency)

    async def _process(article) -> None:
        async with sem:
            try:
                summary = await extract_article_freeform(
                    article.title or "",
                    article.source_url,
                    article.cleaned_text,
                    settings,
                )
            except Exception:
                logger.exception("freeform extraction failed for %s", article.id)
                return
            db.mark_extracted(article.id, {"summary": summary})

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
