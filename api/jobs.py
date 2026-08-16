"""In-memory pipeline job tracking for the dashboard.

Each submitted job runs scrape -> clean -> extract -> synthesize on a background
thread (the underlying `_*_impl` functions are sync and call `asyncio.run`
internally, so they need their own thread rather than the request's event loop).
State lives in a process-local dict — fine for a single-instance dev dashboard,
not durable across restarts or multiple workers.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pyro.cli import _clean_impl, _extract_impl, _synthesize_impl
from pyro.config import Settings
from pyro.db import open_db_from_settings
from pyro.prompts import PipelineMode, build_prompts_config
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls

JobStatus = Literal[
    "pending", "scraping", "cleaning", "extracting", "synthesizing", "done", "error"
]

_STAGE_LABELS: dict[JobStatus, str] = {
    "pending": "Queued",
    "scraping": "Scraping",
    "cleaning": "Cleaning HTML",
    "extracting": "Extracting architecture facts",
    "synthesizing": "Synthesizing docs",
    "done": "Done",
    "error": "Failed",
}


@dataclass
class Job:
    id: str
    company_name: str
    url: str
    limit: int | None
    pipeline_mode: PipelineMode
    extraction_variant: str
    synthesis_variant: str
    status: JobStatus = "pending"
    error: str | None = None
    # "sitemap" (a whole blog crawled from its sitemap.xml) or "article" (a
    # single page URL) — detected automatically once scraping starts.
    source_kind: Literal["sitemap", "article"] | None = None
    discovered_count: int | None = None
    scraped_count: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def status_label(self) -> str:
        return _STAGE_LABELS[self.status]

    @property
    def is_finished(self) -> bool:
        return self.status in ("done", "error")


# Process-local job store, newest first when listed.
JOBS: dict[str, Job] = {}


async def _resolve_urls(
    url: str, settings: Settings
) -> tuple[list[str], Literal["sitemap", "article"]]:
    """A submitted URL is either a sitemap.xml (crawl the whole blog) or a single
    article page (extract just that one). `fetch_sitemap_urls` already raises
    `ValueError` when the response isn't valid sitemap XML — reused here as the
    detection signal instead of duplicating a content-type/extension check."""
    try:
        urls = await fetch_sitemap_urls(url, config=settings.sitemap)
        return urls, "sitemap"
    except ValueError:
        return [url], "article"


def _run_job(job: Job) -> None:
    settings = Settings(
        pipeline_mode=job.pipeline_mode,
        prompts=build_prompts_config(
            job.pipeline_mode, job.extraction_variant, job.synthesis_variant
        ),
    )
    try:
        job.status = "scraping"
        urls, source_kind = asyncio.run(_resolve_urls(job.url, settings))
        job.source_kind = source_kind
        job.discovered_count = len(urls)
        with open_db_from_settings(settings) as database:
            job.scraped_count = asyncio.run(
                scrape_urls(
                    urls,
                    database,
                    job.company_name,
                    limit=job.limit,
                    config=settings.scrape,
                )
            )

        job.status = "cleaning"
        _clean_impl(settings=settings)
        job.status = "extracting"
        _extract_impl(settings=settings)
        job.status = "synthesizing"
        _synthesize_impl(job.company_name, settings=settings)
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)


def submit_job(
    company_name: str,
    url: str,
    limit: int | None,
    pipeline_mode: PipelineMode,
    extraction_variant: str,
    synthesis_variant: str,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        company_name=company_name,
        url=url,
        limit=limit,
        pipeline_mode=pipeline_mode,
        extraction_variant=extraction_variant,
        synthesis_variant=synthesis_variant,
    )
    JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def list_jobs() -> list[Job]:
    return sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)


@dataclass
class SynthRun:
    status: Literal["running", "error"] = "running"
    error: str | None = None


# Process-local, keyed by company_name — one in-flight synthesis run tracked per company so the
# "Run synthesis" button's spinner/disabled state survives the data panel's 4s poll instead of
# being wiped out by the next full-panel re-render (the button used to look inert while a
# multi-minute LLM call ran in the background of a blocking request).
SYNTH_RUNS: dict[str, SynthRun] = {}


def _run_synthesis(company_name: str, settings: Settings) -> None:
    try:
        _synthesize_impl(company_name, settings=settings)
        SYNTH_RUNS.pop(company_name, None)
    except Exception as exc:
        SYNTH_RUNS[company_name] = SynthRun(status="error", error=str(exc))


def submit_synthesis(company_name: str, settings: Settings) -> None:
    SYNTH_RUNS[company_name] = SynthRun(status="running")
    threading.Thread(
        target=_run_synthesis, args=(company_name, settings), daemon=True
    ).start()
