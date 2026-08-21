"""FastAPI app for the pyro dashboard (htmx + Jinja2 + Tailwind).

Run with `make dashboard` or `uv run uvicorn api.main:app --reload`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.deps import DbDep
from api.graph_view import build_graph_elements
from api.jobs import hydrate_jobs, submit_job
from api.render import render_react_flow
from pyro.config import get_settings
from pyro.db import Database, open_db_from_settings
from pyro.prompts import list_variants

load_dotenv()

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Best-effort: a database that isn't reachable yet at startup shouldn't stop the dashboard
    # from serving — every other route already handles a down database gracefully (see
    # _data_context's try/except below). hydrate_jobs repopulates JOBS (still needed by the
    # background job runner in api/jobs.py) and rewrites any job left mid-stage by a prior
    # process's death to "error", so it isn't skipped even though no page lists jobs anymore.
    try:
        with open_db_from_settings(get_settings()) as database:
            hydrate_jobs(database)
    except Exception:
        logger.exception("failed to hydrate job history from the database at startup")
    yield


app = FastAPI(title="pyro dashboard", lifespan=_lifespan)
# mermaid.min.js (~3.5MB uncompressed, static/js/app.js lazy-loads it) is the main beneficiary,
# but this also shrinks every HTML/JSON response and the other vendored JS/CSS at no real CPU
# cost — SSE responses stream below the default minimum_size and are left alone.
app.add_middleware(GZipMiddleware, minimum_size=1000)
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
        request, "index.html", {"template_options": _template_options()}
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
    return templates.TemplateResponse(
        request, "partials/run_started.html", {"job": job}
    )


# Rows per page in the extraction table (dashboard/templates/partials/_panel_extraction.html).
# Kept fixed rather than user-configurable — the panel polls itself every 4s, and a per-request
# page-size choice would need to be threaded through that poll URL for no real benefit yet.
_ARTICLES_PAGE_SIZE = 50


def _data_context(
    db: Database, company: str | None, view: str, page: int = 1
) -> dict:
    view = view if view in ("extraction", "graph") else "extraction"
    page = max(page, 1)
    article_stages = get_settings().article_stages
    try:
        companies = db.list_company_names()
        selected = (
            company if company in companies else (companies[0] if companies else None)
        )
        # Only the extraction view renders articles — skip the fetch entirely for the graph view,
        # which never uses it, rather than paying for a page of article rows on every poll of a
        # tab that can't display them.
        articles, total_articles, total_pages = [], 0, 1
        if selected and view == "extraction":
            articles, total_articles = db.list_article_summaries(
                selected,
                limit=_ARTICLES_PAGE_SIZE,
                offset=(page - 1) * _ARTICLES_PAGE_SIZE,
            )
            total_pages = max(1, -(-total_articles // _ARTICLES_PAGE_SIZE))
            if page > total_pages:
                # Only reachable via a hand-edited URL (a stale page number after articles were
                # deleted elsewhere) — re-fetch once at the corrected offset rather than showing
                # an empty page with a mismatched "Page N of M".
                page = total_pages
                articles, total_articles = db.list_article_summaries(
                    selected,
                    limit=_ARTICLES_PAGE_SIZE,
                    offset=(page - 1) * _ARTICLES_PAGE_SIZE,
                )
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
            "page": 1,
            "total_pages": 1,
            "total_articles": 0,
            "graph": {"entities": [], "relationships": []},
            "graph_html": None,
            "db_error": str(exc),
            "article_stages": article_stages,
        }
    graph_elements = build_graph_elements(graph["entities"], graph["relationships"])
    return {
        "companies": companies,
        "selected": selected,
        "view": view,
        "articles": articles,
        "page": page,
        "total_pages": total_pages,
        "total_articles": total_articles,
        "graph": graph,
        "graph_html": render_react_flow(graph_elements) if graph_elements["nodes"] else None,
        "db_error": None,
        "article_stages": article_stages,
    }


@app.get("/data", response_class=HTMLResponse)
def data_page(
    request: Request,
    db: DbDep,
    company: str | None = None,
    view: str = "extraction",
    page: int = 1,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "data.html", _data_context(db, company, view, page)
    )


@app.get("/data/panel", response_class=HTMLResponse)
def data_panel(
    request: Request,
    db: DbDep,
    company: str | None = None,
    view: str = "extraction",
    page: int = 1,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view, page)
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
    page: int = 1,
) -> HTMLResponse:
    # Ownership check before deleting, so an article id alone can't delete another company's row.
    if db.get_article_for_company(company, article_id) is not None:
        db.delete_article(article_id)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(db, company, view, page)
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
