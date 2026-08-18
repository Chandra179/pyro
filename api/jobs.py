"""In-memory pipeline job tracking for the dashboard.

Each submitted job runs scrape -> clean -> extract -> merge-graph on a background
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

from pyro.cli import _clean_impl, _extract_impl, _merge_graph_impl
from pyro.config import Settings
from pyro.db import open_db_from_settings
from pyro.graph.merge import GraphReporter
from pyro.prompts import build_prompts_config
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls

JobStatus = Literal[
    "pending", "scraping", "cleaning", "extracting", "merging", "done", "error"
]

_STAGE_LABELS: dict[JobStatus, str] = {
    "pending": "Queued",
    "scraping": "Scraping",
    "cleaning": "Cleaning HTML",
    "extracting": "Extracting system map",
    "merging": "Merging into graph",
    "done": "Done",
    "error": "Failed",
}


@dataclass
class GraphMergeCall:
    """One LLM call within a merge run, as seen by the dashboard. `content`/`reasoning` are
    properties over accumulated chunk lists rather than strings appended to directly — `+=` on
    an attribute copies the whole string each time (unlike CPython's in-place-resize
    optimization for a local variable), which would make a fully streamed response O(n^2) in
    its length; appending to a list and joining on read keeps chunk accumulation O(n) and only
    pays the join cost when a template actually renders (a few times a second, not per chunk)."""

    label: str
    model: str = ""
    done: bool = False
    error: str | None = None
    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)


class JobGraphReporter(GraphReporter):
    """Appends each merge call's streamed output onto job.graph_history, so the dashboard can
    render it live (while streaming) and afterward (as history) from the same list. Mutated
    from the job's background thread; read from the request thread — unsynchronized, same
    tradeoff Job.status already accepts for this process-local, single-instance store."""

    def __init__(self, job: Job) -> None:
        self.job = job

    def start_call(self, label: str, model: str) -> None:
        self.job.graph_history.append(GraphMergeCall(label=label, model=model))

    def on_chunk(self, content: str, reasoning: str) -> None:
        call = self.job.graph_history[-1]
        call.content_parts.append(content)
        call.reasoning_parts.append(reasoning)

    def end_call(self, error: str | None = None) -> None:
        call = self.job.graph_history[-1]
        call.done = True
        call.error = error


@dataclass
class Job:
    id: str
    company_name: str
    url: str
    limit: int | None
    extraction_variant: str
    status: JobStatus = "pending"
    error: str | None = None
    # "sitemap" (a whole blog crawled from its sitemap.xml) or "article" (a
    # single page URL) — detected automatically once scraping starts.
    source_kind: Literal["sitemap", "article"] | None = None
    discovered_count: int | None = None
    scraped_count: int | None = None
    graph_history: list[GraphMergeCall] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def status_label(self) -> str:
        return _STAGE_LABELS[self.status]

    @property
    def is_finished(self) -> bool:
        return self.status in ("done", "error")


# Process-local job store, newest first when listed. Bounded because a Job retains every byte of
# every merge call it streamed: an unbounded dict in a long-running dashboard is a slow leak whose
# size is driven by model output, not by job count. Insertion-ordered, so evicting the oldest
# finished job is just walking from the front.
JOBS: dict[str, Job] = {}
MAX_RETAINED_JOBS = 50


def _evict_old_jobs() -> None:
    """Drop the oldest *finished* jobs once the store exceeds MAX_RETAINED_JOBS. Running jobs are
    never evicted regardless of age — their background thread still writes to them, and their card
    is still polling."""
    if len(JOBS) <= MAX_RETAINED_JOBS:
        return
    for job_id, job in list(JOBS.items()):
        if len(JOBS) <= MAX_RETAINED_JOBS:
            break
        if job.is_finished:
            del JOBS[job_id]


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


# Bounds how many jobs actively run at once, across every company — each does Playwright-rendered
# scraping plus a full LLM cascade run, and nothing else in the pipeline throttles that (see
# Settings.max_concurrent_jobs's docstring). Sized once at import time from the default Settings();
# a job submitted beyond the cap blocks at the top of _run_job with its status still "pending"
# (never flips to "scraping") until a slot frees up — an implicit queue the dashboard already
# renders correctly, no new job state needed.
_JOB_SLOTS = threading.Semaphore(Settings().max_concurrent_jobs)


def _run_job(job: Job) -> None:
    with _JOB_SLOTS:
        settings = Settings(prompts=build_prompts_config(job.extraction_variant))
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

            # Scoped to this job's company: the clean/extract stages select work purely by which
            # fields are still null, so two dashboard jobs running at once would otherwise process
            # each other's articles and report each other's counts.
            job.status = "cleaning"
            _clean_impl(settings=settings, company_name=job.company_name)
            job.status = "extracting"
            _extract_impl(settings=settings, company_name=job.company_name)
            job.status = "merging"
            _merge_graph_impl(
                job.company_name,
                settings=settings,
                reporter=JobGraphReporter(job),
            )
            job.status = "done"
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)


def submit_job(
    company_name: str,
    url: str,
    limit: int | None,
    extraction_variant: str,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        company_name=company_name,
        url=url,
        limit=limit,
        extraction_variant=extraction_variant,
    )
    JOBS[job.id] = job
    _evict_old_jobs()
    # Still an unconditional thread per job, deliberately: bounding *active* work (the semaphore
    # inside _run_job) is what protects CPU/LLM capacity. A thread blocked waiting for a slot costs
    # only a stack, and keeps shutdown behavior identical to before (daemon=True, no executor
    # lifecycle to manage) — see Settings.max_concurrent_jobs's docstring for the actual risk this
    # guards against.
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def list_jobs() -> list[Job]:
    return sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
