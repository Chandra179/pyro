"""Pipeline job tracking for the dashboard.

Each submitted job runs scrape -> clean -> extract -> merge-graph on a background
thread (the underlying `_*_impl` functions are sync and call `asyncio.run`
internally, so they need their own thread rather than the request's event loop).

`JOBS` (below) is the live, in-process working set every route reads — a plain dict, same as
before. What changed is durability: each job is also written through to ArangoDB's `jobs`
collection (db/jobs.py) at coarse checkpoints (stage transitions, each merge call finishing), so
`hydrate_jobs` can repopulate `JOBS` from disk when the process restarts instead of starting every
Runs page empty. This is still a single-instance design — JOBS itself, and the concurrency
semaphore below, are process-local and shared by nothing else — persistence buys restart survival,
not multi-worker/horizontal scaling.

A job that's still "scraping"/"cleaning"/"extracting"/"merging" in the database when the process
starts has no surviving thread to finish it (the thread died with the old process), so
`hydrate_jobs` rewrites it to "error" on load rather than leaving it stuck showing a stage it will
never leave.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pyro.cli import _clean_impl, _extract_impl, _merge_graph_impl
from pyro.config import Settings
from pyro.db import Database, open_db_from_settings
from pyro.graph.merge import GraphReporter
from pyro.prompts import build_prompts_config
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls

logger = logging.getLogger(__name__)

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

    def to_doc(self) -> dict[str, Any]:
        """Snapshot for persistence — `content`/`reasoning` are joined here, once, rather than
        storing the raw part-lists: this is only ever written at low frequency (a call finishing,
        not each streamed chunk), so the O(n) join cost that matters during streaming doesn't
        apply here."""
        return {
            "label": self.label,
            "model": self.model,
            "done": self.done,
            "error": self.error,
            "content": self.content,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> GraphMergeCall:
        call = cls(
            label=doc["label"],
            model=doc.get("model", ""),
            done=doc.get("done", False),
            error=doc.get("error"),
        )
        if doc.get("content"):
            call.content_parts.append(doc["content"])
        if doc.get("reasoning"):
            call.reasoning_parts.append(doc["reasoning"])
        return call


class JobGraphReporter(GraphReporter):
    """Appends each merge call's streamed output onto job.graph_history, so the dashboard can
    render it live (while streaming) and afterward (as history) from the same list. Mutated
    from the job's background thread; read from the request thread — unsynchronized, same
    tradeoff Job.status already accepts for this process-local, single-instance store.

    `database`, when given, is written to once a call finishes (not on every streamed chunk — see
    this module's docstring) so a run's merge history survives a restart partway through."""

    def __init__(self, job: Job, database: Database | None = None) -> None:
        self.job = job
        self.database = database

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
        if self.database is not None:
            self.database.save_job(self.job.to_doc())


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

    def to_doc(self) -> dict[str, Any]:
        return {
            "_key": self.id,
            "company_name": self.company_name,
            "url": self.url,
            "limit": self.limit,
            "extraction_variant": self.extraction_variant,
            "status": self.status,
            "error": self.error,
            "source_kind": self.source_kind,
            "discovered_count": self.discovered_count,
            "scraped_count": self.scraped_count,
            "graph_history": [call.to_doc() for call in self.graph_history],
            "created_at": self.created_at,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> Job:
        job = cls(
            id=doc["_key"],
            company_name=doc["company_name"],
            url=doc["url"],
            limit=doc.get("limit"),
            extraction_variant=doc.get("extraction_variant", "default"),
            status=doc.get("status", "error"),
            error=doc.get("error"),
            source_kind=doc.get("source_kind"),
            discovered_count=doc.get("discovered_count"),
            scraped_count=doc.get("scraped_count"),
            created_at=doc.get("created_at", datetime.now(UTC).isoformat()),
        )
        job.graph_history = [
            GraphMergeCall.from_doc(call) for call in doc.get("graph_history", [])
        ]
        return job


# Process-local job store, newest first when listed. Bounded because a Job retains every byte of
# every merge call it streamed: an unbounded dict in a long-running dashboard is a slow leak whose
# size is driven by model output, not by job count. Insertion-ordered, so evicting the oldest
# finished job is just walking from the front.
JOBS: dict[str, Job] = {}
MAX_RETAINED_JOBS = 50


def _evict_old_jobs(database: Database) -> None:
    """Drop the oldest *finished* jobs once the store exceeds MAX_RETAINED_JOBS, from memory and
    from the persisted `jobs` collection alike — otherwise the database log would grow without
    bound even though the in-memory store and the Runs page both cap at MAX_RETAINED_JOBS. Running
    jobs are never evicted regardless of age — their background thread still writes to them, and
    their card is still polling."""
    if len(JOBS) <= MAX_RETAINED_JOBS:
        return
    for job_id, job in list(JOBS.items()):
        if len(JOBS) <= MAX_RETAINED_JOBS:
            break
        if job.is_finished:
            del JOBS[job_id]
            database.delete_job(job_id)


def hydrate_jobs(database: Database) -> None:
    """Repopulate JOBS from the persisted `jobs` collection — called once at app startup
    (api/main.py) so the Runs page and past merge histories survive a dashboard restart instead of
    starting empty every time.

    A job that isn't done/error yet has no surviving thread (the one that would have finished it
    died with the previous process), so it's rewritten to "error" here — and that correction is
    written back to the database too, so it doesn't keep re-appearing as a live-looking stage on
    every subsequent restart."""
    for doc in database.list_jobs(limit=MAX_RETAINED_JOBS):
        job = Job.from_doc(doc)
        if not job.is_finished:
            job.status = "error"
            job.error = "Interrupted by a dashboard restart."
            database.save_job(job.to_doc())
        JOBS[job.id] = job


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
        # One connection for the job's whole lifetime rather than one per stage: the underlying
        # python-arango handle is a thin wrapper over a pooled connection and safe to hold
        # (db/connection.py), and every checkpoint below needs it for `_save()` anyway.
        with open_db_from_settings(settings) as database:

            def _save() -> None:
                # Best-effort: a transient write failure here must not take down a run that's
                # otherwise progressing normally — the in-memory job (and its live SSE stream)
                # stays correct either way, this only affects what a restart would recover.
                try:
                    database.save_job(job.to_doc())
                except Exception:
                    logger.exception("failed to persist job %s", job.id)

            try:
                job.status = "scraping"
                _save()
                urls, source_kind = asyncio.run(_resolve_urls(job.url, settings))
                job.source_kind = source_kind
                job.discovered_count = len(urls)
                job.scraped_count = asyncio.run(
                    scrape_urls(
                        urls,
                        database,
                        job.company_name,
                        limit=job.limit,
                        config=settings.scrape,
                    )
                )
                _save()

                # Scoped to this job's company: the clean/extract stages select work purely by
                # which fields are still null, so two dashboard jobs running at once would
                # otherwise process each other's articles and report each other's counts.
                job.status = "cleaning"
                _save()
                _clean_impl(settings=settings, company_name=job.company_name)
                job.status = "extracting"
                _save()
                _extract_impl(settings=settings, company_name=job.company_name)
                job.status = "merging"
                _save()
                _merge_graph_impl(
                    job.company_name,
                    settings=settings,
                    reporter=JobGraphReporter(job, database),
                )
                job.status = "done"
                _save()
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                _save()


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
    with open_db_from_settings(Settings()) as database:
        database.save_job(job.to_doc())
        _evict_old_jobs(database)
    # Still an unconditional thread per job, deliberately: bounding *active* work (the semaphore
    # inside _run_job) is what protects CPU/LLM capacity. A thread blocked waiting for a slot costs
    # only a stack, and keeps shutdown behavior identical to before (daemon=True, no executor
    # lifecycle to manage) — see Settings.max_concurrent_jobs's docstring for the actual risk this
    # guards against.
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def list_jobs() -> list[Job]:
    return sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
