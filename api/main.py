"""FastAPI app for the pyro dashboard (htmx + Jinja2 + Tailwind).

Run with `make dashboard` or `uv run uvicorn api.main:app --reload`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from api.deps import DbDep
from api.graph_view import build_graph_mermaid
from api.jobs import JOBS, list_jobs, submit_job
from api.render import render_mermaid
from api.sse import graph_history_events
from pyro.db import Database
from pyro.prompts import list_variants

load_dotenv()

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

app = FastAPI(title="pyro dashboard")
app.mount(
    "/static", StaticFiles(directory=str(_DASHBOARD_DIR / "static")), name="static"
)
templates = Jinja2Templates(directory=str(_DASHBOARD_DIR / "templates"))


def _timeago(value: str) -> str:
    """Coarse relative time for a job's `created_at` (see api/jobs.py) — "4m ago" rather than a
    raw ISO timestamp. A running job's card polls its summary every 2s (job_status.html), so this
    stays current for the case that matters; a finished job's card renders once and doesn't need
    to tick live."""
    try:
        then = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return ""
    seconds = int((datetime.now(UTC) - then).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


templates.env.filters["timeago"] = _timeago


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


def _job_or_404(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


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
    return templates.TemplateResponse(
        request, "partials/job_status.html", {"job": _job_or_404(job_id)}
    )


@app.get("/jobs/{job_id}/graph-history", response_class=HTMLResponse)
def job_graph_history(request: Request, job_id: str) -> HTMLResponse:
    """Static render of a run's merge history. The live view streams over
    /jobs/{id}/graph-events instead; this stays as the no-JavaScript/after-the-fact rendering and
    is what a page load of a finished job serves."""
    return templates.TemplateResponse(
        request, "partials/graph_history.html", {"job": _job_or_404(job_id)}
    )


@app.get("/jobs/{job_id}/graph-events")
async def job_graph_events(request: Request, job_id: str) -> EventSourceResponse:
    """Live merge output as server-sent events — see api/sse.py for the event vocabulary."""
    job = _job_or_404(job_id)
    return EventSourceResponse(graph_history_events(request, job, templates))


def _data_context(db: Database, company: str | None, view: str) -> dict:
    view = view if view in ("extraction", "graph") else "extraction"
    try:
        companies = db.list_company_names()
        selected = (
            company if company in companies else (companies[0] if companies else None)
        )
        articles = db.list_articles(selected) if selected else []
        graph = (
            {
                "entities": db.list_entities(selected),
                "relationships": db.list_relationships(selected),
            }
            if selected
            else {"entities": [], "relationships": []}
        )
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
    request: Request,
    db: DbDep,
    company: str | None = None,
    view: str = "extraction",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "data.html", _data_context(db, company, view)
    )


@app.get("/data/panel", response_class=HTMLResponse)
def data_panel(
    request: Request,
    db: DbDep,
    company: str | None = None,
    view: str = "extraction",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view)
    )


@app.get("/data/article/{article_id}", response_class=HTMLResponse)
def article_preview(
    request: Request, db: DbDep, article_id: str, company: str
) -> HTMLResponse:
    article = db.get_article_for_company(company, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    return templates.TemplateResponse(
        request, "partials/article_modal.html", {"article": article}
    )


@app.delete("/data/article/{article_id}", response_class=HTMLResponse)
def delete_article_route(
    request: Request,
    db: DbDep,
    article_id: str,
    company: str,
    view: str = "extraction",
) -> HTMLResponse:
    # Ownership check before deleting, so an article id alone can't delete another company's row.
    if db.get_article_for_company(company, article_id) is not None:
        db.delete_article(article_id)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view)
    )


@app.delete("/data/articles", response_class=HTMLResponse)
def delete_all_articles_route(
    request: Request, db: DbDep, company: str, view: str = "extraction"
) -> HTMLResponse:
    db.delete_articles_for_company(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view)
    )


@app.delete("/data/graph", response_class=HTMLResponse)
def delete_graph_route(
    request: Request, db: DbDep, company: str, view: str = "graph"
) -> HTMLResponse:
    db.delete_graph_for_company(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view)
    )
