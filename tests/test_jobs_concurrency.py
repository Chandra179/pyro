"""api/jobs.py's _JOB_SLOTS: bounds how many dashboard pipeline jobs actively run at once, across
every company. Without this, several companies' jobs submitted around the same time would all run
fully concurrently — each doing Playwright-rendered scraping and driving the same LLM cascade with
nothing throttling total load."""

import threading
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock

import api.jobs as jobs_module
from api.jobs import Job, _run_job


@contextmanager
def _fake_open_db(settings):
    yield object()


async def _fake_resolve_urls(url, settings):
    return [url], "article"


def _make_job(i: int) -> Job:
    return Job(
        id=str(i),
        company_name=f"company-{i}",
        url="https://example.com/sitemap.xml",
        limit=None,
        extraction_variant="default",
    )


def test_run_job_respects_the_concurrency_cap(monkeypatch):
    concurrent = 0
    max_concurrent = 0
    lock = threading.Lock()

    def _track_and_sleep(*args, **kwargs):
        nonlocal concurrent, max_concurrent
        with lock:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)
        with lock:
            concurrent -= 1

    monkeypatch.setattr(jobs_module, "_JOB_SLOTS", threading.Semaphore(2))
    monkeypatch.setattr(jobs_module, "_resolve_urls", _fake_resolve_urls)
    monkeypatch.setattr(jobs_module, "open_db_from_settings", _fake_open_db)
    monkeypatch.setattr(jobs_module, "scrape_urls", AsyncMock(return_value=0))
    monkeypatch.setattr(jobs_module, "_clean_impl", lambda **kwargs: None)
    monkeypatch.setattr(jobs_module, "_extract_impl", lambda **kwargs: None)
    monkeypatch.setattr(jobs_module, "_merge_graph_impl", _track_and_sleep)

    jobs = [_make_job(i) for i in range(5)]
    threads = [threading.Thread(target=_run_job, args=(job,)) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(job.status == "done" for job in jobs)
    # Actually ran concurrently, not accidentally serialized by the test setup...
    assert max_concurrent > 1
    # ...but never beyond the configured cap.
    assert max_concurrent <= 2


def test_job_slots_defaults_to_settings_max_concurrent_jobs():
    from pyro.config import Settings

    assert jobs_module._JOB_SLOTS._value == Settings().max_concurrent_jobs
