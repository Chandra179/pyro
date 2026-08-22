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


def test_index_renders_run_form():
    client = _client(_StubDb())
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'hx-post="/jobs"' in resp.text
    assert 'id="run-feedback"' in resp.text


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
    resp = client.get("/data/panel", params={"company_name": "Stripe", "view": "graph"})
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
    resp = client.get("/data/panel", params={"company_name": "Netflix", "view": "graph"})
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
    resp = client.get("/data/panel", params={"company_name": "Netflix", "view": "graph"})
    assert resp.status_code == 200
    # Rendered inside an HTML-escaped data-elements JSON attribute, so the space is the literal
    # humanized text (no HTML entity involved, unlike the old Mermaid arrow syntax).
    assert "writes to" in resp.text


def test_extraction_panel_polls_but_graph_panel_does_not():
    """Only the extraction view self-refreshes — the graph view is an interactive React Flow
    graph the viewer may be mid-pan/zoom/drag on, so it offers an explicit Refresh link instead."""
    client = _client(_StubDb(companies=["Netflix"]))
    extraction = client.get("/data/panel", params={"company_name": "Netflix"})
    graph = client.get("/data/panel", params={"company_name": "Netflix", "view": "graph"})
    assert "every 4s" in extraction.text
    assert "every 4s" not in graph.text


def test_data_shell_reachable_via_hx_select_target():
    client = _client(_StubDb())
    resp = client.get("/data")
    assert resp.status_code == 200
    assert 'id="data-shell"' in resp.text


def test_job_submission_returns_confirmation_not_a_job_card(monkeypatch):
    """Recent Runs was removed entirely — submitting a job now gets a one-shot acknowledgment
    instead of a live-updating card, since progress/output live in ArangoDB, not the browser.

    submit_job itself (api/jobs.py) opens a real database connection and starts a background
    thread that scrapes the given URL — out of scope for this route test, so it's stubbed rather
    than exercised, same as the database access other routes get via the DbDep override."""
    from api.jobs import Job

    def _fake_submit_job(company_name, url, limit, extraction_variant):
        return Job(
            id="job-1",
            company_name=company_name,
            url=url,
            limit=limit,
            extraction_variant=extraction_variant,
        )

    monkeypatch.setattr(main, "submit_job", _fake_submit_job)
    client = _client(_StubDb())
    resp = client.post(
        "/jobs",
        data={"company_name": "Acme", "url": "https://example.com/sitemap.xml"},
    )
    assert resp.status_code == 200
    assert "Started for" in resp.text
    assert "Acme" in resp.text


def test_static_assets_served():
    client = _client(_StubDb())
    for path in (
        "/static/css/app.css",
        "/static/js/htmx.min.js",
        "/static/js/graph-island.bundle.js",
        "/static/css/react-flow.css",
        "/static/js/app.js",
    ):
        assert client.get(path).status_code == 200, path
