from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict

import typer
from dotenv import load_dotenv

from pyro.clean.clean import clean_html
from pyro.config import Settings
from pyro.db import open_db_from_settings
from pyro.extract.pipeline import run_extraction
from pyro.freeform.pipeline import run_freeform_extraction
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls
from pyro.synth.freeform import run_freeform_synthesis
from pyro.synth.structured import run_structured_synthesis

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = typer.Typer(help="Engineering blog architecture synthesis pipeline")

# Each pipeline stage has a plain `_impl` function taking an optional `Settings` override, and a
# thin `@app.command()` wrapper with no `settings` param — typer can't build a CLI parser for the
# Settings type, so it must never appear in a typer-decorated function's signature. Programmatic
# callers (run_pipeline.py, run_all below) call the `_impl` functions directly.


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


def _clean_impl(limit: int | None = None, settings: Settings | None = None) -> None:
    """Strip boilerplate and collapse code blocks for all un-cleaned articles."""
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        articles = database.fetch_unprocessed("clean", limit=limit)
        for article in articles:
            cleaned = clean_html(
                article.raw_html, settings.code_block_line_threshold, settings.clean
            )
            database.mark_cleaned(article.id, cleaned)
        typer.echo(f"cleaned {len(articles)} articles")


@app.command()
def clean(limit: int | None = typer.Option(None)) -> None:
    """Strip boilerplate and collapse code blocks for all un-cleaned articles."""
    _clean_impl(limit=limit)


def _extract_impl(limit: int | None = None, settings: Settings | None = None) -> None:
    """Run LLM extraction on all cleaned, unextracted articles (extraction itself isn't scoped
    to a company — see the 'synthesize' step, which is)."""
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        if settings.pipeline_mode == "freeform":
            count = asyncio.run(
                run_freeform_extraction(database, settings, limit=limit)
            )
        else:
            count = asyncio.run(run_extraction(database, settings, limit=limit))
    typer.echo(f"extracted {count} articles")


@app.command()
def extract(limit: int | None = typer.Option(None)) -> None:
    """Run LLM extraction on all cleaned, unextracted articles."""
    _extract_impl(limit=limit)


class SynthesisInProgress(Exception):
    """Raised when synthesis is already running for this company_name."""


# One lock per company_name, shared by every caller of _synthesize_impl (the full
# scrape->clean->extract->synthesize job in api/jobs.py and the dashboard's standalone
# "Run synthesis" button both funnel through here). Guarding at this choke point — rather than
# in each caller separately — is what makes it impossible for two synthesis runs to race on the
# same company's docs, regardless of which entry point triggered them. Freeform mode's
# full delete-then-rebuild (see run_freeform_synthesis) makes concurrent runs actively
# destructive, not just wasteful: interleaved deletes/upserts can leave duplicate topic docs
# behind, as happened for a Netflix "GenRec" article routed under two different AI-chosen
# filenames by two racing runs.
_SYNTH_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _synthesize_impl(company_name: str, settings: Settings | None = None) -> None:
    """Synthesize architecture docs for company_name from its already-extracted articles and
    persist them to ArangoDB — structured mode groups by domain and always rebuilds; freeform
    mode incrementally routes only not-yet-routed articles into topic files (to force a full
    freeform rebuild, e.g. after changing the routing prompt, delete the company's docs first —
    see run_freeform_synthesis). Raises SynthesisInProgress instead of running if synthesis is
    already in flight for this company_name."""
    lock = _SYNTH_LOCKS[company_name]
    if not lock.acquire(blocking=False):
        raise SynthesisInProgress(company_name)
    try:
        settings = settings or Settings()
        with open_db_from_settings(settings) as database:
            if settings.pipeline_mode == "freeform":
                docs = asyncio.run(
                    run_freeform_synthesis(database, settings, company_name)
                )
                if docs:
                    for filename in docs:
                        typer.echo(f"wrote doc {filename}.md")
                else:
                    typer.echo("no new articles to route; docs already up to date")
            else:
                docs = asyncio.run(
                    run_structured_synthesis(database, settings, company_name)
                )
                for domain in docs:
                    typer.echo(
                        f"wrote doc architecture-{domain.lower().replace(' ', '-')} (domain: {domain})"
                    )
    finally:
        lock.release()


@app.command()
def synthesize(company_name: str = typer.Option(...)) -> None:
    """Synthesize one architecture doc per domain for company_name and persist to ArangoDB."""
    _synthesize_impl(company_name)


def _synthesize_pending_impl(settings: Settings | None = None) -> None:
    """Freeform mode: run synthesize for every company that has at least one unrouted extracted
    article. Meant to be invoked on a schedule (see cron/) as a replacement for the dashboard's
    manual "Run synthesis" button — safe to call repeatedly since run_freeform_synthesis is
    incremental/idempotent (a company with nothing new to route is a fast no-op, not a full
    rebuild). Skips a company outright if SynthesisInProgress (e.g. a pipeline job already
    running for it) rather than failing the whole batch."""
    settings = settings or Settings()
    if settings.pipeline_mode != "freeform":
        typer.echo(
            f"pipeline_mode={settings.pipeline_mode!r}; synthesize-pending only supports "
            "freeform mode (structured mode has no per-article routing state to detect "
            "'pending' from — see Database.list_companies_with_pending_synthesis)"
        )
        raise typer.Exit(code=1)
    with open_db_from_settings(settings) as database:
        companies = database.list_companies_with_pending_synthesis()
    if not companies:
        typer.echo("no companies with pending synthesis")
        return
    for company_name in companies:
        try:
            _synthesize_impl(company_name, settings=settings)
        except SynthesisInProgress:
            typer.echo(f"{company_name}: synthesis already in progress, skipping")
        except Exception as exc:
            typer.echo(f"{company_name}: synthesis failed: {exc}")


@app.command(name="synthesize-pending")
def synthesize_pending() -> None:
    """Freeform mode: run synthesize for every company with unrouted extracted articles.
    Intended for cron/scheduled invocation (see cron/) instead of the dashboard's manual button."""
    _synthesize_pending_impl()


@app.command()
def docs(company_name: str = typer.Option(...)) -> None:
    """List synthesized/routed architecture docs stored in ArangoDB for company_name."""
    settings = Settings()
    with open_db_from_settings(settings) as database:
        for doc in database.list_docs(company_name):
            typer.echo(f"{doc['_key']}.md  ({doc.get('heading') or ''})")


def _run_all_impl(
    company_name: str,
    sitemap_url: str,
    concurrency: int | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
) -> None:
    """Run scrape -> clean -> extract -> synthesize end-to-end."""
    _scrape_impl(
        company_name,
        sitemap_url,
        concurrency=concurrency,
        limit=limit,
        settings=settings,
    )
    _clean_impl(settings=settings)
    _extract_impl(settings=settings)
    _synthesize_impl(company_name, settings=settings)


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
    """Run scrape -> clean -> extract -> synthesize end-to-end."""
    _run_all_impl(company_name, sitemap_url, concurrency=concurrency, limit=limit)


if __name__ == "__main__":
    app()
