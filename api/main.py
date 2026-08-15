"""FastAPI app for the pyro dashboard (htmx + Jinja2 + Tailwind).

Run with `make dashboard` or `uv run uvicorn api.main:app --reload`.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from api.jobs import JOBS, list_jobs, submit_job

load_dotenv()

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

app = FastAPI(title="pyro dashboard")
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
