"""FastAPI app for the pyro dashboard (htmx + Jinja2 + Tailwind).

Run with `make dashboard` or `uv run uvicorn api.main:app --reload`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.data import (
    delete_all_articles,
    delete_article,
    delete_graph,
    get_article,
    get_extraction,
    get_graph,
    list_companies,
)
from api.graph_view import build_graph_mermaid
from api.jobs import JOBS, list_jobs, submit_job
from api.render import render_mermaid
from pyro.prompts import list_variants

load_dotenv()

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

app = FastAPI(title="pyro dashboard")
app.mount(
    "/static", StaticFiles(directory=str(_DASHBOARD_DIR / "static")), name="static"
)
templates = Jinja2Templates(directory=str(_DASHBOARD_DIR / "templates"))


def _template_choices() -> list[dict]:
    """Selectable extraction prompt variants for the run form's "Prompt template" dropdown —
    there's only ever a "default" variant today, but this stays data-driven so a new variant
    directory under prompts/extraction/ shows up automatically, no code change needed."""
    return [{"value": v, "label": v.capitalize()} for v in list_variants("extraction")]


def _template_options() -> dict:
    return {"templates": _template_choices()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": list_jobs(), "template_options": _template_options()},
    )


def _resolve_template(raw: str) -> str:
    """Validate the submitted variant against the real template choices and return it. Raises
    400 rather than trusting it directly, since it's joined into file paths downstream
    (build_prompts_config)."""
    valid = {c["value"] for c in _template_choices()}
    if raw not in valid:
        raise HTTPException(status_code=400, detail="invalid template")
    return raw


@app.post("/jobs", response_class=HTMLResponse)
def create_job(
    request: Request,
    company_name: str = Form(...),
    url: str = Form(...),
    limit: int | None = Form(None),
    template: str = Form("default"),
) -> HTMLResponse:
    variant = _resolve_template(template)

    job = submit_job(company_name.strip(), url.strip(), limit, variant)
    return templates.TemplateResponse(request, "partials/job_status.html", {"job": job})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str) -> HTMLResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return templates.TemplateResponse(request, "partials/job_status.html", {"job": job})


@app.get("/jobs/{job_id}/graph-history", response_class=HTMLResponse)
def job_graph_history(request: Request, job_id: str) -> HTMLResponse:
    """Polled every 1s (see partials/graph_history.html) while a job is merging, to show each
    LLM call's output streaming in — separately from the outer job card's slower 2s stage poll,
    since this needs finer granularity only during that one stage."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return templates.TemplateResponse(
        request, "partials/graph_history.html", {"job": job}
    )


def _data_context(company: str | None, view: str) -> dict:
    view = view if view in ("extraction", "graph") else "extraction"
    try:
        companies = list_companies()
        selected = (
            company if company in companies else (companies[0] if companies else None)
        )
        articles = get_extraction(selected) if selected else []
        graph = get_graph(selected) if selected else {"entities": [], "relationships": []}
    except Exception as exc:
        logger.exception("Data view failed to load from the database")
        return {
            "companies": [],
            "selected": None,
            "view": view,
            "articles": [],
            "graph": {"entities": [], "relationships": []},
            "graph_html": None,
            "db_error": str(exc),
        }
    graph_source = build_graph_mermaid(graph["entities"], graph["relationships"])
    return {
        "companies": companies,
        "selected": selected,
        "view": view,
        "articles": articles,
        "graph": graph,
        "graph_html": render_mermaid(graph_source) if graph_source else None,
        "db_error": None,
    }


@app.get("/data", response_class=HTMLResponse)
def data_page(
    request: Request, company: str | None = None, view: str = "extraction"
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "data.html", _data_context(company, view)
    )


@app.get("/data/panel", response_class=HTMLResponse)
def data_panel(
    request: Request, company: str | None = None, view: str = "extraction"
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.get("/data/article/{article_id}", response_class=HTMLResponse)
def article_preview(request: Request, article_id: str, company: str) -> HTMLResponse:
    article = get_article(company, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    return templates.TemplateResponse(
        request, "partials/article_modal.html", {"article": article}
    )


@app.delete("/data/article/{article_id}", response_class=HTMLResponse)
def delete_article_route(
    request: Request, article_id: str, company: str, view: str = "extraction"
) -> HTMLResponse:
    delete_article(company, article_id)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.delete("/data/articles", response_class=HTMLResponse)
def delete_all_articles_route(
    request: Request, company: str, view: str = "extraction"
) -> HTMLResponse:
    delete_all_articles(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.delete("/data/graph", response_class=HTMLResponse)
def delete_graph_route(
    request: Request, company: str, view: str = "graph"
) -> HTMLResponse:
    delete_graph(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )
