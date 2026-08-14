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
from pyro.freeform.pipeline import run_freeform_extraction
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls
from pyro.synth.pipeline import run_synthesis, slugify

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = typer.Typer(help="Engineering blog architecture synthesis pipeline")


@app.command()
def scrape(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    db: Path = typer.Option(...),
    concurrency: int | None = typer.Option(None, help="Defaults to config/config.yaml scrape.concurrency"),
    limit: int | None = typer.Option(None),
    settings: Settings | None = None,
) -> None:
    """Discover URLs from sitemap.xml and render+store raw HTML."""
    settings = settings or Settings()

    async def _run() -> None:
        urls = await fetch_sitemap_urls(sitemap_url, config=settings.sitemap)
        typer.echo(f"discovered {len(urls)} URLs from sitemap")
        with open_db(db) as database:
            count = await scrape_urls(
                urls, database, company_name, concurrency=concurrency, limit=limit, config=settings.scrape
            )
        typer.echo(f"scraped {count} new articles")

    asyncio.run(_run())


@app.command()
def clean(
    db: Path = typer.Option(...),
    limit: int | None = typer.Option(None),
    settings: Settings | None = None,
) -> None:
    """Strip boilerplate and collapse code blocks for all un-cleaned articles."""
    settings = settings or Settings()
    with open_db(db) as database:
        articles = database.fetch_unprocessed("clean", limit=limit)
        for article in articles:
            cleaned = clean_html(article.raw_html, settings.code_block_line_threshold, settings.clean)
            database.mark_cleaned(article.id, cleaned)
        typer.echo(f"cleaned {len(articles)} articles")


@app.command()
def extract(
    db: Path = typer.Option(...),
    company_name: str | None = typer.Option(None, help="Required when config pipeline_mode is 'freeform'"),
    out_dir: Path = typer.Option(Path("output"), help="freeform mode only: where topic .md files are written"),
    limit: int | None = typer.Option(None),
    settings: Settings | None = None,
) -> None:
    """Run LLM extraction on all cleaned, unextracted articles.

    In freeform pipeline_mode, each article is immediately routed into a topic file under
    out_dir/ (updating an existing one or creating a new one) as it's extracted, instead of a
    separate synthesize step.
    """
    settings = settings or Settings()
    with open_db(db) as database:
        if settings.pipeline_mode == "freeform":
            if not company_name:
                raise typer.BadParameter("--company-name is required when pipeline_mode is 'freeform'")
            count = asyncio.run(
                run_freeform_extraction(database, settings, company_name, out_dir, limit=limit)
            )
        else:
            count = asyncio.run(run_extraction(database, settings, limit=limit))
    typer.echo(f"extracted {count} articles")


@app.command()
def synthesize(
    db: Path = typer.Option(...),
    company_name: str = typer.Option(...),
    out_dir: Path = typer.Option(Path("output")),
    settings: Settings | None = None,
) -> None:
    """Synthesize one architecture doc per domain for company_name into out_dir/."""
    settings = settings or Settings()
    if settings.pipeline_mode == "freeform":
        typer.echo("pipeline_mode is 'freeform' — topic files are updated during 'extract'; nothing to do.")
        return
    with open_db(db) as database:
        docs = asyncio.run(run_synthesis(database, settings, company_name))
    out_dir.mkdir(parents=True, exist_ok=True)
    for domain, content in docs.items():
        path = out_dir / f"architecture-{slugify(domain)}.md"
        path.write_text(content)
        typer.echo(f"wrote {path}")


@app.command(name="run-all")
def run_all(
    company_name: str = typer.Option(...),
    sitemap_url: str = typer.Option(...),
    db: Path = typer.Option(...),
    out_dir: Path = typer.Option(Path("output")),
    concurrency: int | None = typer.Option(None, help="Defaults to config/config.yaml scrape.concurrency"),
    limit: int | None = typer.Option(None, help="Cap articles scraped, for sample validation runs"),
    settings: Settings | None = None,
) -> None:
    """Run scrape -> clean -> extract -> synthesize end-to-end."""
    scrape(
        company_name=company_name,
        sitemap_url=sitemap_url,
        db=db,
        concurrency=concurrency,
        limit=limit,
        settings=settings,
    )
    clean(db=db, settings=settings)
    extract(db=db, company_name=company_name, out_dir=out_dir, settings=settings)
    synthesize(db=db, company_name=company_name, out_dir=out_dir, settings=settings)


if __name__ == "__main__":
    app()
