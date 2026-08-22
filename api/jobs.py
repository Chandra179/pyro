"""Pipeline job tracking for the dashboard.

Each submitted job runs scrape -> clean -> extract -> merge-graph on a background thread, calling
each stage's own orchestration function directly (clean/pipeline.py, extract/pipeline.py,
graph/merge.py) rather than going through pyro.cli — the CLI's typer commands are one caller of
those functions, not a layer the dashboard should have to route through. The async ones
(scrape_urls, run_extraction) are run via `asyncio.run` per call since this all happens on a
background thread, not the request's event loop.

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

from pyro.clean.pipeline import run_cleaning
from pyro.config import Settings
from pyro.db import Database, open_db_from_settings
from pyro.extract.pipeline import run_extraction
from pyro.graph.merge import GraphReporter, run_graph_merge_exclusive
from pyro.prompts import build_prompts_config
from pyro.scrape.fetch import scrape_urls
from pyro.scrape.sitemap import fetch_sitemap_urls

logger = logging.getLogger(__name__)

JobStatus = Literal[
    "pending", "scraping", "cleaning", "extracting", "merging", "done", "error"
]


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
    # Populated by report_summary once entities/relationships are actually upserted — None until
    # then (including for the whole life of a call that errors before reaching that point), so
    # templates can tell "no summary yet" apart from "summary is zero".
    new_entities: int | None = None
    matched_entities: int | None = None
    relationships_count: int | None = None
    llm_resolved_count: int | None = None

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
            "new_entities": self.new_entities,
            "matched_entities": self.matched_entities,
            "relationships_count": self.relationships_count,
            "llm_resolved_count": self.llm_resolved_count,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> GraphMergeCall:
        call = cls(
            label=doc["label"],
            model=doc.get("model", ""),
            done=doc.get("done", False),
            error=doc.get("error"),
            new_entities=doc.get("new_entities"),
            matched_entities=doc.get("matched_entities"),
            relationships_count=doc.get("relationships_count"),
            llm_resolved_count=doc.get("llm_resolved_count"),
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

    def report_summary(
        self,
        new_entities: int,
        matched_entities: int,
        relationships_count: int,
        llm_resolved_count: int,
    ) -> None:
        call = self.job.graph_history[-1]
        call.new_entities = new_entities
        call.matched_entities = matched_entities
        call.relationships_count = relationships_count
        call.llm_resolved_count = llm_resolved_count

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


# Process-local job store, newest first when listed. Bounded: a Job retains every byte of every
# merge call it streamed, so an unbounded dict would slow-leak with model output, not job count.
JOBS: dict[str, Job] = {}
MAX_RETAINED_JOBS = Settings().max_retained_jobs


def _evict_old_jobs(database: Database) -> None:
    """Drop the oldest *finished* jobs past MAX_RETAINED_JOBS, from memory and the `jobs`
    collection alike. Running jobs are never evicted — their thread still writes to them."""
    if len(JOBS) <= MAX_RETAINED_JOBS:
        return
    for job_id, job in list(JOBS.items()):
        if len(JOBS) <= MAX_RETAINED_JOBS:
            break
        if job.is_finished:
            del JOBS[job_id]
            database.delete_job(job_id)


def hydrate_jobs(database: Database) -> None:
    """Repopulate JOBS from the persisted `jobs` collection at startup (api/main.py) so past runs
    survive a dashboard restart. A job that isn't done/error yet has no surviving thread, so it's
    rewritten to "error" and that correction is saved back too."""
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
    """A submitted URL is either a sitemap.xml (crawl the whole blog) or a single article page.
    Reuses fetch_sitemap_urls' ValueError on invalid XML as the detection signal."""
    try:
        urls = await fetch_sitemap_urls(url, config=settings.sitemap)
        return urls, "sitemap"
    except ValueError:
        return [url], "article"


# Bounds concurrently running jobs across every company (see Settings.max_concurrent_jobs). A job
# submitted beyond the cap blocks here with status still "pending" until a slot frees up.
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
                run_cleaning(database, settings, company_name=job.company_name)
                job.status = "extracting"
                _save()
                asyncio.run(
                    run_extraction(database, settings, company_name=job.company_name)
                )
                job.status = "merging"
                _save()
                run_graph_merge_exclusive(
                    database,
                    settings,
                    job.company_name,
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
    # Unconditional thread per job, deliberately: the semaphore inside _run_job bounds *active*
    # work, and a thread blocked waiting for a slot costs only a stack.
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job


def list_jobs() -> list[Job]:
    return sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)
