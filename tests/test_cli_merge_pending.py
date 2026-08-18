"""merge-graph-pending's cross-company concurrency (cli.py's _merge_graph_pending_impl).

Each company's own merge is already serialized correctly by _MERGE_LOCKS — what's being tested
here is the newer property: *different* companies run concurrently, bounded by
settings.merge_pending_concurrency, rather than one after another. A sequential loop would make
one cron tick's wall-clock time scale linearly with company count."""

import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

from pyro.cli import _merge_graph_pending_impl
from pyro.config import Settings


class _FakeDb:
    def __init__(self, companies):
        self._companies = companies

    def list_companies_with_pending_merge(self):
        return self._companies


@contextmanager
def _fake_open_db(settings):
    yield _FakeDb(["c0", "c1", "c2", "c3", "c4", "c5"])


def test_merge_pending_runs_companies_concurrently_up_to_the_cap():
    calls: list[str] = []
    concurrent = 0
    max_concurrent = 0
    lock = threading.Lock()

    def fake_merge_graph_impl(company_name, settings=None):
        nonlocal concurrent, max_concurrent
        with lock:
            calls.append(company_name)
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)
        with lock:
            concurrent -= 1

    settings = Settings(_env_file=None, merge_pending_concurrency=3)

    with (
        patch("pyro.cli.open_db_from_settings", _fake_open_db),
        patch("pyro.cli._merge_graph_impl", side_effect=fake_merge_graph_impl),
    ):
        _merge_graph_pending_impl(settings=settings)

    # Every company still gets merged exactly once...
    assert sorted(calls) == ["c0", "c1", "c2", "c3", "c4", "c5"]
    # ...but not one after another (the whole point of this change)...
    assert max_concurrent > 1
    # ...and never beyond the configured cap.
    assert max_concurrent <= 3


def test_merge_pending_is_a_noop_with_nothing_pending():
    @contextmanager
    def _empty_db(settings):
        yield _FakeDb([])

    settings = Settings(_env_file=None, merge_pending_concurrency=3)
    with (
        patch("pyro.cli.open_db_from_settings", _empty_db),
        patch("pyro.cli._merge_graph_impl") as mock_merge,
    ):
        _merge_graph_pending_impl(settings=settings)

    mock_merge.assert_not_called()
