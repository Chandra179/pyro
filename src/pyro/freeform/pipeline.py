"""Freeform mode: plain-text extraction, each article immediately routed into a topic file in
the output directory (existing file updated, or a new one created) instead of a separate batch
synthesis pass."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pyro.config import Settings
from pyro.db import Database
from pyro.extract.pipeline import extract_article_freeform
from pyro.router import synthesis_model_params
from pyro.synth.pipeline import route_and_update_doc

logger = logging.getLogger(__name__)


def _build_files_index(out_dir: Path) -> str:
    """One line per existing topic file: filename + its first heading, as context for routing."""
    files = sorted(out_dir.glob("*.md"))
    if not files:
        return "(none yet — this will be the first file)"

    lines = []
    for path in files:
        heading = next(
            (line.lstrip("#").strip() for line in path.read_text().splitlines() if line.startswith("#")),
            path.stem,
        )
        lines.append(f"- {path.name}: {heading}")
    return "\n".join(lines)


async def run_freeform_extraction(
    db: Database, settings: Settings, company_name: str, out_dir: Path, limit: int | None = None
) -> int:
    """Extract each unprocessed article, then route it into out_dir: fold it into an existing
    topic file or create a new one. Returns count processed."""
    articles = db.fetch_unprocessed("extract", limit=limit)
    if not articles:
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(settings.extraction_concurrency)
    # Extraction runs concurrently; the directory scan + routing decision + write is a critical
    # section serialized by this lock so concurrent articles don't race on the same file or both
    # decide to create a new file for what should be one topic.
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
            db.mark_extracted(article.id, True, {"summary": summary})

        async with doc_lock:
            files_index = _build_files_index(out_dir)
            filename, content = await route_and_update_doc(
                files_index,
                article.title or article.source_url,
                summary,
                company_name,
                settings,
                synth_params,
            )
            (out_dir / filename).write_text(content)

    await asyncio.gather(*(_process(a) for a in articles))
    return len(articles)
