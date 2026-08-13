from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from dotenv import load_dotenv

from pyro.clean.clean import clean_html
from pyro.config import Settings
from pyro.db import open_db
from pyro.extract.pipeline import run_extraction
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls
from pyro.synth.pipeline import run_synthesis

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = typer.Typer(help="Engineering blog architecture synthesis pipeline")


@app.command()
def scrape(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    db: Path = typer.Option(...),
    concurrency: int = typer.Option(5),
    limit: int | None = typer.Option(None),
) -> None:
    """Discover URLs from sitemap.xml and render+store raw HTML."""

    async def _run() -> None:
        urls = await fetch_sitemap_urls(sitemap_url)
        typer.echo(f"discovered {len(urls)} URLs from sitemap")
        with open_db(db) as database:
            count = await scrape_urls(
                urls, database, company_name, concurrency=concurrency, limit=limit
            )
        typer.echo(f"scraped {count} new articles")

    asyncio.run(_run())


@app.command()
def clean(
    db: Path = typer.Option(...),
    limit: int | None = typer.Option(None),
) -> None:
    """Strip boilerplate and collapse code blocks for all un-cleaned articles."""
    settings = Settings()
    with open_db(db) as database:
        articles = database.fetch_unprocessed("clean", limit=limit)
        for article in articles:
            cleaned = clean_html(article.raw_html, settings.code_block_line_threshold)
            database.mark_cleaned(article.id, cleaned)
        typer.echo(f"cleaned {len(articles)} articles")


@app.command()
def extract(
    db: Path = typer.Option(...),
    limit: int | None = typer.Option(None),
) -> None:
    """Run LLM extraction on all cleaned, unextracted articles."""
    settings = Settings()
    with open_db(db) as database:
        count = asyncio.run(run_extraction(database, settings, limit=limit))
    typer.echo(f"extracted {count} articles")


@app.command()
def synthesize(
    db: Path = typer.Option(...),
    company_name: str = typer.Option(...),
    out: Path = typer.Option(Path("architecture.md")),
) -> None:
    """Merge all architectural facts for company_name into architecture.md."""
    settings = Settings()
    with open_db(db) as database:
        content = asyncio.run(run_synthesis(database, settings, company_name))
    out.write_text(content)
    typer.echo(f"wrote {out}")


@app.command(name="run-all")
def run_all(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    db: Path = typer.Option(...),
    out: Path = typer.Option(Path("architecture.md")),
    concurrency: int = typer.Option(5),
    limit: int | None = typer.Option(None, help="Cap articles scraped, for sample validation runs"),
) -> None:
    """Run scrape -> clean -> extract -> synthesize end-to-end."""
    scrape(
        company_name=company_name,
        sitemap_url=sitemap_url,
        db=db,
        concurrency=concurrency,
        limit=limit,
    )
    clean(db=db)
    extract(db=db)
    synthesize(db=db, company_name=company_name, out=out)


if __name__ == "__main__":
    app()
