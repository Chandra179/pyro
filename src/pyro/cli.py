from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import typer
from dotenv import load_dotenv

from pyro.clean.clean import clean_html
from pyro.config import Settings
from pyro.db import open_db_from_settings
from pyro.extract.pipeline import run_extraction
from pyro.graph.backfill import canonicalize_relations
from pyro.graph.merge import GraphReporter, run_graph_merge
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = typer.Typer(help="Engineering blog architecture-graph pipeline")

# Each pipeline stage has a plain `_impl` function taking an optional `Settings` override, and a
# thin `@app.command()` wrapper with no `settings` param — typer can't build a CLI parser for the
# Settings type, so it must never appear in a typer-decorated function's signature. Programmatic
# callers (run_pipeline.py, run_all below, and api/jobs.py's background job runner) call the
# `_impl` functions directly — the underscore marks "not a typer command," not "private to this
# module"; these are the real internal API other packages are meant to call.


def _scrape_impl(
    company_name: str,
    sitemap_url: str,
    concurrency: int | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
) -> None:
    """Discover URLs from sitemap.xml and render+store raw HTML in ArangoDB."""
    settings = settings or Settings()

    async def _run() -> None:
        urls = await fetch_sitemap_urls(sitemap_url, config=settings.sitemap)
        typer.echo(f"discovered {len(urls)} URLs from sitemap")
        with open_db_from_settings(settings) as database:
            count = await scrape_urls(
                urls,
                database,
                company_name,
                concurrency=concurrency,
                limit=limit,
                config=settings.scrape,
            )
        typer.echo(f"scraped {count} new articles")

    asyncio.run(_run())


@app.command()
def scrape(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    concurrency: int | None = typer.Option(
        None, help="Defaults to config/config.yaml scrape.concurrency"
    ),
    limit: int | None = typer.Option(None),
) -> None:
    """Discover URLs from sitemap.xml and render+store raw HTML in ArangoDB."""
    _scrape_impl(company_name, sitemap_url, concurrency=concurrency, limit=limit)


def _clean_impl(
    limit: int | None = None,
    settings: Settings | None = None,
    company_name: str | None = None,
) -> None:
    """Strip boilerplate and collapse code blocks for un-cleaned articles. Scoped to
    `company_name` when given — callers running one company's pipeline should pass it so
    concurrent runs don't consume each other's articles."""
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        articles = database.fetch_unprocessed(
            "clean", limit=limit, company_name=company_name
        )
        for article in articles:
            cleaned = clean_html(
                article.raw_html, settings.code_block_line_threshold, settings.clean
            )
            database.mark_cleaned(article.id, cleaned)
        typer.echo(f"cleaned {len(articles)} articles")


@app.command()
def clean(
    limit: int | None = typer.Option(None),
    company_name: str | None = typer.Option(None, help="Limit to one company's articles"),
) -> None:
    """Strip boilerplate and collapse code blocks for un-cleaned articles."""
    _clean_impl(limit=limit, company_name=company_name)


def _extract_impl(
    limit: int | None = None,
    settings: Settings | None = None,
    company_name: str | None = None,
) -> None:
    """Run LLM extraction (entity/relationship graph) on cleaned, unextracted articles, optionally
    scoped to one company."""
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        count = asyncio.run(
            run_extraction(database, settings, limit=limit, company_name=company_name)
        )
    typer.echo(f"extracted {count} articles")


@app.command()
def extract(
    limit: int | None = typer.Option(None),
    company_name: str | None = typer.Option(None, help="Limit to one company's articles"),
) -> None:
    """Run LLM extraction on cleaned, unextracted articles."""
    _extract_impl(limit=limit, company_name=company_name)


class GraphMergeInProgress(Exception):
    """Raised when a graph merge is already running for this company_name."""


# One lock per company_name, shared by every caller of _merge_graph_impl (the full
# scrape->clean->extract->merge-graph job in api/jobs.py and the dashboard's standalone
# "Run merge" button both funnel through here). Guarding at this choke point — rather than in
# each caller separately — is what makes it impossible for two merge runs to race on the same
# company's entity graph, regardless of which entry point triggered them.
_MERGE_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _merge_graph_impl(
    company_name: str,
    settings: Settings | None = None,
    reporter: GraphReporter | None = None,
) -> None:
    """Fold company_name's not-yet-merged extracted articles into its entity graph and persist
    to ArangoDB. Raises GraphMergeInProgress instead of running if a merge is already in flight
    for this company_name. `reporter` (default no-op) is streamed each merge call's output as it
    arrives — see api/jobs.py for the dashboard's use of it."""
    lock = _MERGE_LOCKS[company_name]
    if not lock.acquire(blocking=False):
        raise GraphMergeInProgress(company_name)
    try:
        settings = settings or Settings()
        with open_db_from_settings(settings) as database:
            result = asyncio.run(
                run_graph_merge(database, settings, company_name, reporter)
            )
        if result["articles_merged"]:
            typer.echo(
                f"merged {result['articles_merged']} articles "
                f"({result['entities']} entities, {result['relationships']} relationships total)"
            )
        else:
            typer.echo("no new articles to merge; graph already up to date")
    finally:
        lock.release()


@app.command(name="merge-graph")
def merge_graph(company_name: str = typer.Option(...)) -> None:
    """Fold company_name's not-yet-merged extracted articles into its entity graph."""
    _merge_graph_impl(company_name)


def _merge_graph_pending_impl(settings: Settings | None = None) -> None:
    """Run merge-graph for every company that has at least one unmerged extracted article. Meant
    to be invoked on a schedule (see cron/) as a replacement for the dashboard's manual "Run
    merge" button — safe to call repeatedly since run_graph_merge is incremental/idempotent (a
    company with nothing new to merge is a fast no-op, not a full rebuild). Skips a company
    outright if GraphMergeInProgress (e.g. a pipeline job already running for it) rather than
    failing the whole batch.

    Companies run up to `settings.merge_pending_concurrency` at a time, not one after another —
    each company's own merge is already serialized correctly by `_MERGE_LOCKS`, so concurrent
    *different* companies never touches that invariant. Sequential-only becomes a real problem as
    company count grows: one cron tick's wall-clock time would otherwise scale linearly with how
    many companies exist, and can eventually exceed the schedule interval (see cron/README.md)."""
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        companies = database.list_companies_with_pending_merge()
    if not companies:
        typer.echo("no companies with pending graph merge")
        return

    def _merge_one(company_name: str) -> None:
        try:
            _merge_graph_impl(company_name, settings=settings)
        except GraphMergeInProgress:
            typer.echo(f"{company_name}: merge already in progress, skipping")
        except Exception as exc:
            typer.echo(f"{company_name}: merge failed: {exc}")

    workers = min(settings.merge_pending_concurrency, len(companies))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_merge_one, companies))


@app.command(name="merge-graph-pending")
def merge_graph_pending() -> None:
    """Run merge-graph for every company with unmerged extracted articles. Intended for
    cron/scheduled invocation (see cron/) instead of the dashboard's manual button."""
    _merge_graph_pending_impl()


@app.command()
def graph(company_name: str = typer.Option(...)) -> None:
    """List the entity graph stored in ArangoDB for company_name."""
    settings = Settings()
    with open_db_from_settings(settings) as database:
        entities = database.list_entities(company_name)
        relationships = database.list_relationships(company_name)
    for entity in entities:
        typer.echo(f"[{entity['kind']}] {entity['name']} ({entity['domain']})")
    typer.echo("")
    for rel in relationships:
        suffix = f" (as_of={rel['as_of']})" if rel.get("as_of") else ""
        typer.echo(f"{rel['source']} --{rel['relation']}--> {rel['target']}{suffix}")


@app.command(name="canonicalize-relations")
def canonicalize_relations_cmd(
    company_name: str | None = typer.Option(
        None, help="Defaults to every company in the graph"
    ),
) -> None:
    """One-off: rewrite already-stored edges onto the controlled relation vocabulary, collapsing
    synonym duplicates ("sends requests to" / "calls") into single edges. Only needed for graphs
    merged before the vocabulary existed — new extractions are canonical already."""
    settings = Settings()
    with open_db_from_settings(settings) as database:
        companies = [company_name] if company_name else database.list_company_names()
        for name in companies:
            result = canonicalize_relations(database, name)
            typer.echo(
                f"{name}: rewrote {result['rewritten']}/{result['examined']} relations "
                f"({result['collapsed']} duplicate edges collapsed)"
            )


def _run_all_impl(
    company_name: str,
    sitemap_url: str,
    concurrency: int | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
) -> None:
    """Run scrape -> clean -> extract -> merge-graph end-to-end."""
    _scrape_impl(
        company_name,
        sitemap_url,
        concurrency=concurrency,
        limit=limit,
        settings=settings,
    )
    _clean_impl(settings=settings, company_name=company_name)
    _extract_impl(settings=settings, company_name=company_name)
    _merge_graph_impl(company_name, settings=settings)


@app.command(name="run-all")
def run_all(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    concurrency: int | None = typer.Option(
        None, help="Defaults to config/config.yaml scrape.concurrency"
    ),
    limit: int | None = typer.Option(
        None, help="Cap articles scraped, for sample validation runs"
    ),
) -> None:
    """Run scrape -> clean -> extract -> merge-graph end-to-end."""
    _run_all_impl(company_name, sitemap_url, concurrency=concurrency, limit=limit)


if __name__ == "__main__":
    app()
