"""Cleaning-stage orchestration: fetch un-cleaned articles, run clean_html, persist.

Mirrors extract/pipeline.py::run_extraction and graph/merge.py::run_graph_merge — each pipeline
stage owns a plain orchestration function here rather than that loop living only in cli.py, so
callers other than the CLI (api/jobs.py's background job runner) can drive the stage without
importing pyro.cli.
"""

from __future__ import annotations

from pyro.clean.clean import clean_html
from pyro.config import Settings
from pyro.db import Database


def run_cleaning(
    db: Database,
    settings: Settings,
    limit: int | None = None,
    company_name: str | None = None,
) -> int:
    """Strip boilerplate and collapse code blocks for un-cleaned articles, optionally scoped to
    one company. Returns count processed."""
    articles = db.fetch_unprocessed("clean", limit=limit, company_name=company_name)
    for article in articles:
        cleaned = clean_html(article.raw_html, settings.code_block_line_threshold, settings.clean)
        db.mark_cleaned(article.id, cleaned)
    return len(articles)
