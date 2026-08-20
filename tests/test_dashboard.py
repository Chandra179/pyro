"""Dashboard route coverage.

The database is supplied through FastAPI's dependency system (api/deps.get_db), so these override
that one dependency with a stub instead of monkeypatching module-level accessor functions — which
is the practical payoff of routes taking a `db` rather than reaching for a global.
"""

import pytest
from fastapi.testclient import TestClient

from api import main
from api.deps import get_db


class _StubDb:
    def __init__(self, companies=(), articles=(), entities=(), relationships=()):
        self._companies = list(companies)
        self._articles = list(articles)
        self._entities = list(entities)
        self._relationships = list(relationships)

    def list_company_names(self):
        return self._companies

    def list_articles(self, company_name):
        return self._articles

    def list_article_summaries(self, company_name, limit, offset):
        page = self._articles[offset : offset + limit]
        return list(page), len(self._articles)

    def list_entities(self, company_name):
        return self._entities

    def list_relationships(self, company_name):
        return self._relationships


class _BrokenDb:
    def list_company_names(self):
        raise RuntimeError("connection refused")


def _client(db) -> TestClient:
    main.app.dependency_overrides[get_db] = lambda: db
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    main.app.dependency_overrides.clear()


def test_index_renders_no_runs():
    client = _client(_StubDb())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No runs yet." in resp.text


def test_data_page_shows_db_error_without_raising():
    client = _client(_BrokenDb())
    resp = client.get("/data")
    assert resp.status_code == 200
    assert "Couldn't reach the database" in resp.text
    assert "connection refused" in resp.text


def test_data_page_lists_companies_and_defaults_to_extraction():
    client = _client(_StubDb(companies=["Netflix", "Stripe"]))
    resp = client.get("/data")
    assert resp.status_code == 200
    assert "Netflix" in resp.text
    assert "No articles scraped yet for Netflix." in resp.text


def test_data_panel_respects_selected_company_and_view():
    client = _client(_StubDb(companies=["Netflix", "Stripe"]))
    resp = client.get("/data/panel", params={"company": "Stripe", "view": "graph"})
    assert resp.status_code == 200
    assert "Entity graph · Stripe" in resp.text


def test_data_panel_renders_react_flow_graph_when_graph_has_entities():
    client = _client(
        _StubDb(
            companies=["Netflix"],
            entities=[
                {"name": "Cassandra", "kind": "datastore", "domain": "Data Platform"}
            ],
        )
    )
    resp = client.get("/data/panel", params={"company": "Netflix", "view": "graph"})
    assert resp.status_code == 200
    assert 'class="react-flow-graph not-prose"' in resp.text
    assert "Cassandra" in resp.text


def test_graph_view_labels_use_humanized_relation():
    client = _client(
        _StubDb(
            companies=["Netflix"],
            entities=[
                {"name": "API", "kind": "service", "domain": "Other"},
                {"name": "Cassandra", "kind": "datastore", "domain": "Other"},
            ],
            relationships=[
                {"source": "API", "target": "Cassandra", "relation": "writes_to"}
            ],
        )
    )
    resp = client.get("/data/panel", params={"company": "Netflix", "view": "graph"})
    assert resp.status_code == 200
    # Rendered inside an HTML-escaped data-elements JSON attribute, so the space is the literal
    # humanized text (no HTML entity involved, unlike the old Mermaid arrow syntax).
    assert "writes to" in resp.text


def test_extraction_panel_polls_but_graph_panel_does_not():
    """Only the extraction view self-refreshes — the graph view is an interactive React Flow
    graph the viewer may be mid-pan/zoom/drag on, so it offers an explicit Refresh link instead."""
    client = _client(_StubDb(companies=["Netflix"]))
    extraction = client.get("/data/panel", params={"company": "Netflix"})
    graph = client.get("/data/panel", params={"company": "Netflix", "view": "graph"})
    assert "every 4s" in extraction.text
    assert "every 4s" not in graph.text


def test_data_shell_reachable_via_hx_select_target():
    client = _client(_StubDb())
    resp = client.get("/data")
    assert resp.status_code == 200
    assert 'id="data-shell"' in resp.text


def test_graph_history_streams_while_merging_and_is_static_once_done():
    """Two rendering modes off one template: a live run subscribes to the SSE stream, a finished
    one just paints its history with no subscriptions to leak."""
    from api.jobs import JOBS, GraphMergeCall, Job

    job = Job(
        id="job-under-test",
        company_name="Acme",
        url="https://example.com",
        limit=None,
        extraction_variant="default",
        status="merging",
    )
    call = GraphMergeCall(label="Post One", model="m")
    call.content_parts.append("partial output")
    job.graph_history.append(call)
    JOBS[job.id] = job
    try:
        client = _client(_StubDb())

        live = client.get(f"/jobs/{job.id}/graph-history").text
        assert 'sse-connect="/jobs/job-under-test/graph-events"' in live
        assert 'sse-close="stream-close"' in live

        # The enclosing card renders the history outside the element that polls, so a poll can
        # never re-process (and re-subscribe) the live subtree.
        card = client.get(f"/jobs/{job.id}").text
        assert 'hx-select="#job-job-under-test-summary"' in card
        summary_start = card.index('id="job-job-under-test-summary"')
        assert card.index("graph-history-job-under-test") > summary_start
        assert "sse-connect" not in card[summary_start : card.index("graph-history-job-under-test")]

        job.status = "done"
        finished = client.get(f"/jobs/{job.id}/graph-history").text
        assert "sse-connect" not in finished
        assert "sse-swap" not in finished
        assert "Post One" in finished
        assert "partial output" in finished
    finally:
        JOBS.pop(job.id, None)


def test_unknown_job_is_404():
    client = _client(_StubDb())
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/jobs/nope/graph-history").status_code == 404


def test_static_assets_served():
    client = _client(_StubDb())
    for path in (
        "/static/css/app.css",
        "/static/js/htmx.min.js",
        "/static/js/htmx-ext-sse.min.js",
        "/static/js/graph-island.bundle.js",
        "/static/css/react-flow.css",
        "/static/js/app.js",
    ):
        assert client.get(path).status_code == 200, path
