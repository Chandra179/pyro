from fastapi.testclient import TestClient

from api import main


def test_index_renders_no_runs():
    client = TestClient(main.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No runs yet." in resp.text


def test_data_page_shows_db_error_without_raising(monkeypatch):
    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main, "list_companies", boom)
    client = TestClient(main.app)
    resp = client.get("/data")
    assert resp.status_code == 200
    assert "Couldn't reach the database" in resp.text
    assert "connection refused" in resp.text


def test_data_page_lists_companies_and_defaults_to_extraction(monkeypatch):
    monkeypatch.setattr(main, "list_companies", lambda: ["Netflix", "Stripe"])
    monkeypatch.setattr(main, "get_extraction", lambda company: [])
    monkeypatch.setattr(main, "get_synthesis", lambda company: [])
    client = TestClient(main.app)
    resp = client.get("/data")
    assert resp.status_code == 200
    assert "Netflix" in resp.text
    assert "No articles scraped yet for Netflix." in resp.text


def test_data_panel_respects_selected_company_and_view(monkeypatch):
    monkeypatch.setattr(main, "list_companies", lambda: ["Netflix", "Stripe"])
    monkeypatch.setattr(main, "get_extraction", lambda company: [])
    monkeypatch.setattr(main, "get_synthesis", lambda company: [])
    client = TestClient(main.app)
    resp = client.get("/data/panel", params={"company": "Stripe", "view": "synthesis"})
    assert resp.status_code == 200
    assert "Synthesized docs · Stripe" in resp.text


def test_data_shell_reachable_via_hx_select_target():
    client = TestClient(main.app)
    resp = client.get("/data")
    assert resp.status_code == 200
    assert 'id="data-shell"' in resp.text


def test_static_assets_served():
    client = TestClient(main.app)
    css = client.get("/static/css/app.css")
    js = client.get("/static/js/htmx.min.js")
    assert css.status_code == 200
    assert js.status_code == 200
