#!/usr/bin/env python3
"""One-file pipeline runner: scrape -> clean -> extract -> merge-graph.

Edit the constants below to point at a different company/blog, then run it
via `make run` or `uv run python run_pipeline.py`.

Tuning knobs (model cascade, concurrency, chunking, cleaning, graph-merge
model, etc.) default to config/config.yaml — override any of them here via
OVERRIDES instead of editing that file. Field names/nesting match
src/pyro/config.py:Settings, e.g. {"extraction_concurrency": 2} or
{"scrape": {"concurrency": 3}}.
"""

from pyro.cli import _run_all_impl
from pyro.config import Settings

# --- Edit these to point at a different company/blog ---
COMPANY_NAME = "Netflix"
SITEMAP_URL = "https://netflixtechblog.com/sitemap/sitemap.xml"
LIMIT = 20  # cap on newly-scraped articles per run; None for the full blog
CONCURRENCY = None  # None -> config/config.yaml's scrape.concurrency

# --- Config overrides (optional) — only set the keys you want to change ---
OVERRIDES: dict = {
    # "extraction_concurrency": 2,
    # "graph_model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
}

if __name__ == "__main__":
    _run_all_impl(
        company_name=COMPANY_NAME,
        sitemap_url=SITEMAP_URL,
        concurrency=CONCURRENCY,
        limit=LIMIT,
        settings=Settings(**OVERRIDES) if OVERRIDES else None,
    )
