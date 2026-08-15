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

from api.data import get_extraction, get_synthesis, list_companies
from api.jobs import JOBS, list_jobs, submit_job

load_dotenv()

logger = logging.getLogger(__name__)

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

app = FastAPI(title="pyro dashboard")
app.mount("/static", StaticFiles(directory=str(_DASHBOARD_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_DASHBOARD_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"jobs": list_jobs()}
    )


@app.post("/jobs", response_class=HTMLResponse)
def create_job(
    request: Request,
    company_name: str = Form(...),
    url: str = Form(...),
    limit: int | None = Form(None),
) -> HTMLResponse:
    job = submit_job(company_name.strip(), url.strip(), limit)
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
        }
    return {
        "companies": companies,
        "selected": selected,
        "view": view,
        "articles": articles,
        "docs": docs,
        "db_error": None,
    }


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request, company: str | None = None, view: str = "extraction") -> HTMLResponse:
    return templates.TemplateResponse(request, "data.html", _data_context(company, view))


@app.get("/data/panel", response_class=HTMLResponse)
def data_panel(request: Request, company: str | None = None, view: str = "extraction") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/data_panel.html", _data_context(company, view)
    )
