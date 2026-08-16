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


def get_doc(company_name: str, doc_key: str) -> dict | None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        return db.get_doc_for_company(company_name, doc_key)


def get_article(company_name: str, article_id: str) -> Article | None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        return db.get_article_for_company(company_name, article_id)


def delete_article(company_name: str, article_id: str) -> None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        article = db.get_article_for_company(company_name, article_id)
        if article is not None:
            db.delete_article(article_id)


def delete_all_articles(company_name: str) -> None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        db.delete_articles_for_company(company_name)


def delete_doc(company_name: str, doc_key: str) -> None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        doc = db.get_doc_for_company(company_name, doc_key)
        if doc is not None:
            db.delete_doc(doc_key, company_name)


def delete_all_docs(company_name: str) -> None:
    settings = Settings()
    with open_db_from_settings(settings) as db:
        db.delete_docs_for_company(company_name)
