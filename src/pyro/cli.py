from __future__ import annotations

import asyncio
import logging

import typer
from dotenv import load_dotenv

from pyro.clean.clean import clean_html
from pyro.config import Settings
from pyro.db import open_db_from_settings
from pyro.extract.pipeline import run_extraction
from pyro.freeform.pipeline import run_freeform_extraction
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls
from pyro.synth.pipeline import run_synthesis

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
                urls, database, company_name, concurrency=concurrency, limit=limit, config=settings.scrape
            )
        typer.echo(f"scraped {count} new articles")

    asyncio.run(_run())


@app.command()
def scrape(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    concurrency: int | None = typer.Option(None, help="Defaults to config/config.yaml scrape.concurrency"),
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
            cleaned = clean_html(article.raw_html, settings.code_block_line_threshold, settings.clean)
            database.mark_cleaned(article.id, cleaned)
        typer.echo(f"cleaned {len(articles)} articles")


@app.command()
def clean(limit: int | None = typer.Option(None)) -> None:
    """Strip boilerplate and collapse code blocks for all un-cleaned articles."""
    _clean_impl(limit=limit)


def _extract_impl(
    company_name: str | None = None, limit: int | None = None, settings: Settings | None = None
) -> None:
    """Run LLM extraction on all cleaned, unextracted articles.

    In freeform pipeline_mode, each article is immediately routed into a topic doc in
    ArangoDB (updating an existing one or creating a new one) as it's extracted, instead of a
    separate synthesize step.
    """
    settings = settings or Settings()
    with open_db_from_settings(settings) as database:
        if settings.pipeline_mode == "freeform":
            if not company_name:
                raise typer.BadParameter("--company-name is required when pipeline_mode is 'freeform'")
            count = asyncio.run(run_freeform_extraction(database, settings, company_name, limit=limit))
        else:
            count = asyncio.run(run_extraction(database, settings, limit=limit))
    typer.echo(f"extracted {count} articles")


@app.command()
def extract(
    company_name: str | None = typer.Option(None, help="Required when config pipeline_mode is 'freeform'"),
    limit: int | None = typer.Option(None),
) -> None:
    """Run LLM extraction on all cleaned, unextracted articles."""
    _extract_impl(company_name=company_name, limit=limit)


def _synthesize_impl(company_name: str, settings: Settings | None = None) -> None:
    """Synthesize one architecture doc per domain for company_name and persist to ArangoDB."""
    settings = settings or Settings()
    if settings.pipeline_mode == "freeform":
        typer.echo("pipeline_mode is 'freeform' — topic docs are updated during 'extract'; nothing to do.")
        return
    with open_db_from_settings(settings) as database:
        docs = asyncio.run(run_synthesis(database, settings, company_name))
    for domain in docs:
        typer.echo(f"wrote doc architecture-{domain.lower().replace(' ', '-')} (domain: {domain})")


@app.command()
def synthesize(company_name: str = typer.Option(...)) -> None:
    """Synthesize one architecture doc per domain for company_name and persist to ArangoDB."""
    _synthesize_impl(company_name)


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
    _scrape_impl(company_name, sitemap_url, concurrency=concurrency, limit=limit, settings=settings)
    _clean_impl(settings=settings)
    _extract_impl(company_name=company_name, settings=settings)
    _synthesize_impl(company_name, settings=settings)


@app.command(name="run-all")
def run_all(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    concurrency: int | None = typer.Option(None, help="Defaults to config/config.yaml scrape.concurrency"),
    limit: int | None = typer.Option(None, help="Cap articles scraped, for sample validation runs"),
) -> None:
    """Run scrape -> clean -> extract -> synthesize end-to-end."""
    _run_all_impl(company_name, sitemap_url, concurrency=concurrency, limit=limit)


if __name__ == "__main__":
    app()
