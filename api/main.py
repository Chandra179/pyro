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
    delete_all_docs,
    delete_article,
    delete_doc,
    get_article,
    get_doc,
    get_extraction,
    get_synthesis,
    list_companies,
)
from api.jobs import JOBS, SYNTH_RUNS, list_jobs, submit_job, submit_synthesis
from api.render import render_markdown
from pyro.config import Settings
from pyro.prompts import list_variants

load_dotenv()

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

app = FastAPI(title="pyro dashboard")
app.mount("/static", StaticFiles(directory=str(_DASHBOARD_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_DASHBOARD_DIR / "templates"))


def _template_choices() -> list[dict]:
    """Selectable prompt templates for the run form's single "Prompt templates" dropdown.

    Extraction and synthesis are always chosen together as one pair, keyed by mode — there's
    only ever a "default" variant per stage today, so a single mode choice fully determines both
    stages' prompt files. If per-stage variants beyond "default" show up later, this needs to
    grow back into a picker per stage; until then, one dropdown is all the real choice there is.
    """
    return [
        {"value": mode, "label": mode.capitalize()}
        for mode in ("structured", "freeform")
        if "default" in list_variants("extraction", mode) and "default" in list_variants("synthesis", mode)
    ]


def _template_options() -> dict:
    return {"templates": _template_choices()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"jobs": list_jobs(), "template_options": _template_options()}
    )


def _resolve_template(raw: str) -> str:
    """Validate the submitted mode against the real template choices and return it. Raises 400
    rather than trusting it directly, since it's joined into file paths downstream
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
    template: str = Form("structured"),
) -> HTMLResponse:
    mode = _resolve_template(template)

    job = submit_job(company_name.strip(), url.strip(), limit, mode, "default", "default")
    return templates.TemplateResponse(
        request, "partials/job_status.html", {"job": job}
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str) -> HTMLResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return templates.TemplateResponse(
        request, "partials/job_status.html", {"job": job}
    )


def _data_context(company: str | None, view: str) -> dict:
    view = view if view in ("extraction", "synthesis") else "extraction"
    try:
        companies = list_companies()
        selected = company if company in companies else (companies[0] if companies else None)
        articles = get_extraction(selected) if selected else []
        docs = get_synthesis(selected) if selected else []
    except Exception as exc:
        logger.exception("Data view failed to load from the database")
        return {
            "companies": [],
            "selected": None,
            "view": view,
            "articles": [],
            "docs": [],
            "db_error": str(exc),
            "synth_running": False,
            "synth_error": None,
        }
    synth_run = SYNTH_RUNS.get(selected) if selected else None
    return {
        "companies": companies,
        "selected": selected,
        "view": view,
        "articles": articles,
        "docs": docs,
        "db_error": None,
        "synth_running": synth_run is not None and synth_run.status == "running",
        "synth_error": synth_run.error if synth_run is not None and synth_run.status == "error" else None,
    }


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request, company: str | None = None, view: str = "extraction") -> HTMLResponse:
    return templates.TemplateResponse(request, "data.html", _data_context(company, view))


@app.get("/data/panel", response_class=HTMLResponse)
def data_panel(request: Request, company: str | None = None, view: str = "extraction") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.get("/data/doc/{doc_key}", response_class=HTMLResponse)
def doc_preview(request: Request, doc_key: str, company: str) -> HTMLResponse:
    doc = get_doc(company, doc_key)
    if doc is None:
        raise HTTPException(status_code=404, detail="doc not found")
    return templates.TemplateResponse(
        request,
        "partials/doc_modal.html",
        {"doc": doc, "content_html": render_markdown(doc["content"])},
    )


@app.get("/data/article/{article_id}", response_class=HTMLResponse)
def article_preview(request: Request, article_id: str, company: str) -> HTMLResponse:
    article = get_article(company, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    return templates.TemplateResponse(request, "partials/article_modal.html", {"article": article})


@app.delete("/data/article/{article_id}", response_class=HTMLResponse)
def delete_article_route(
    request: Request, article_id: str, company: str, view: str = "extraction"
) -> HTMLResponse:
    delete_article(company, article_id)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.delete("/data/articles", response_class=HTMLResponse)
def delete_all_articles_route(request: Request, company: str, view: str = "extraction") -> HTMLResponse:
    delete_all_articles(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.delete("/data/doc/{doc_key}", response_class=HTMLResponse)
def delete_doc_route(request: Request, doc_key: str, company: str, view: str = "synthesis") -> HTMLResponse:
    delete_doc(company, doc_key)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.delete("/data/docs", response_class=HTMLResponse)
def delete_all_docs_route(request: Request, company: str, view: str = "synthesis") -> HTMLResponse:
    delete_all_docs(company)
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )


@app.post("/data/synthesize", response_class=HTMLResponse)
def synthesize_route(request: Request, company: str, view: str = "synthesis") -> HTMLResponse:
    """Re-run synthesis for company from its already-extracted architectural articles — lets a
    user regenerate docs after deleting them, without re-scraping/re-extracting from scratch.

    Runs on a background thread (see api.jobs.submit_synthesis) rather than blocking this
    request: a real LLM synthesis pass can take minutes (chunk batches + rate-limit retries),
    and the data panel's own 4s auto-refresh would otherwise repeatedly re-render the button as
    idle mid-request, making a slow-but-working click look like it did nothing.
    """
    settings = Settings()
    if settings.pipeline_mode == "freeform":
        context = _data_context(company, view)
        context["synth_error"] = (
            "pipeline_mode is 'freeform' — docs are updated as part of extraction, not a "
            "separate synthesis step. Re-run extraction to regenerate them."
        )
        return templates.TemplateResponse(request, "partials/data_panel.html", context)

    existing = SYNTH_RUNS.get(company)
    if existing is None or existing.status != "running":
        submit_synthesis(company, settings)
    context = _data_context(company, view)
    return templates.TemplateResponse(request, "partials/data_panel.html", context)
