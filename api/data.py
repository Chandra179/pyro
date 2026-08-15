"""Read-only accessors for the dashboard's Data view (extraction + synthesis, live
from ArangoDB). Kept separate from api/jobs.py since this reads committed pipeline
state rather than tracking an in-flight job.
"""

from __future__ import annotations

from pyro.config import Settings
from pyro.db import Article, open_db_from_settings


def list_companies() -> list[str]:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        return db.list_company_names()


def get_extraction(company_name: str) -> list[Article]:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        return db.list_articles(company_name)


def get_synthesis(company_name: str) -> list[dict]:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        return db.list_docs(company_name)
