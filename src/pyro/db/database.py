"""`Database` — the facade every caller talks to; composes the repositories, owns no query logic."""

from __future__ import annotations

from typing import Any, Self

from pyro.db.articles import ArticleRepository
from pyro.db.connection import ConnectionParams, connect
from pyro.db.entities import EntityRepository
from pyro.db.jobs import JobRepository
from pyro.db.models import Article
from pyro.db.relationships import RelationshipRepository


class Database:
    def __init__(
        self,
        host: str = "http://localhost:8529",
        database: str = "pyro",
        username: str = "root",
        password: str = "",
        articles_collection: str = "articles",
        entities_collection: str = "entities",
        relationships_collection: str = "relationships",
        jobs_collection: str = "jobs",
    ):
        params = ConnectionParams(
            host=host,
            database=database,
            username=username,
            password=password,
            articles_collection=articles_collection,
            entities_collection=entities_collection,
            relationships_collection=relationships_collection,
            jobs_collection=jobs_collection,
        )
        self._db = connect(params)
        self.articles = ArticleRepository(self._db, articles_collection)
        self.entities = EntityRepository(self._db, entities_collection)
        self.relationships = RelationshipRepository(
            self._db, relationships_collection, entities_collection
        )
        self.jobs = JobRepository(self._db, jobs_collection)

    # No-op: the connection is process-wide and pooled. Kept so `with open_db(...)` reads naturally.
    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- articles: raw scrape -> clean -> extract pipeline state ---

    def upsert_raw(
        self, id: str, source_url: str, title: str | None, company_name: str, raw_html: str
    ) -> None:
        self.articles.upsert_raw(id, source_url, title, company_name, raw_html)

    def exists(self, id: str) -> bool:
        return self.articles.exists(id)

    def mark_cleaned(self, id: str, cleaned_text: str) -> None:
        self.articles.mark_cleaned(id, cleaned_text)

    def mark_extracted(self, id: str, extracted_graph: dict[str, Any]) -> None:
        self.articles.mark_extracted(id, extracted_graph)

    def mark_graph_merged(self, id: str) -> None:
        self.articles.mark_graph_merged(id)

    def fetch_unprocessed(
        self, stage: str, limit: int | None = None, company_name: str | None = None
    ) -> list[Article]:
        return self.articles.fetch_unprocessed(stage, limit=limit, company_name=company_name)

    def fetch_extracted(self, company_name: str) -> list[Article]:
        return self.articles.fetch_extracted(company_name)

    def fetch_pending_merge(self, company_name: str) -> list[Article]:
        return self.articles.fetch_pending_merge(company_name)

    def list_articles(self, company_name: str) -> list[Article]:
        return self.articles.list_articles(company_name)

    def list_article_summaries(
        self, company_name: str, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        return self.articles.list_summaries(company_name, limit, offset)

    def get_article_for_company(self, company_name: str, article_id: str) -> Article | None:
        return self.articles.get_for_company(company_name, article_id)

    def delete_article(self, article_id: str) -> None:
        self.articles.delete(article_id)

    def delete_articles_for_company(self, company_name: str) -> None:
        self.articles.delete_for_company(company_name)

    def list_companies_with_pending_merge(self) -> list[str]:
        return self.articles.list_companies_with_pending_merge()

    def list_company_names(self) -> list[str]:
        """Distinct company names across articles and entities. Unioned in Python rather than
        AQL's UNION_DISTINCT so each side uses its own company_name index."""
        names = set(self.articles.list_company_names())
        names.update(self.entities.list_company_names())
        return sorted(n for n in names if n)

    # --- entities/relationships: the company-wide graph merged from extracted articles ---

    def upsert_entity(
        self,
        company_name: str,
        name: str,
        kind: str,
        domain: str,
        alias: str | None = None,
        first_seen_article_id: str | None = None,
        description: str | None = None,
        alias_method: str | None = None,
    ) -> str:
        return self.entities.upsert(
            company_name,
            name,
            kind,
            domain,
            alias,
            first_seen_article_id,
            description=description,
            alias_method=alias_method,
        )

    def upsert_relationship(
        self,
        company_name: str,
        source: str,
        target: str,
        relation: str,
        as_of: str | None,
        source_article_id: str | None,
        relation_phrase: str | None = None,
        extra_source_article_ids: list[str] | None = None,
    ) -> None:
        self.relationships.upsert(
            company_name,
            source,
            target,
            relation,
            as_of,
            source_article_id,
            relation_phrase=relation_phrase,
            extra_source_article_ids=extra_source_article_ids,
        )

    def invalidate_outgoing_relationships(
        self,
        company_name: str,
        source: str,
        at: str,
        exclude_relation: str | None = None,
    ) -> int:
        return self.relationships.invalidate_outgoing(
            company_name, source, at, exclude_relation=exclude_relation
        )

    def list_entity_names(self, company_name: str) -> list[str]:
        return self.entities.list_names(company_name)

    def list_entities(self, company_name: str) -> list[dict]:
        return self.entities.list_all(company_name)

    def list_relationships(self, company_name: str) -> list[dict]:
        return self.relationships.list_all(company_name)

    def delete_graph_for_company(self, company_name: str) -> None:
        """Deletes all entities/relationships for company_name and resets graph_merged_at on its
        articles, so the next merge run rebuilds from scratch."""
        self.articles.reset_graph_merged(company_name)
        self.relationships.delete_for_company(company_name)
        self.entities.delete_for_company(company_name)

    # --- jobs: dashboard pipeline-run history (api/jobs.py) ---

    def save_job(self, doc: dict[str, Any]) -> None:
        self.jobs.save(doc)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.jobs.list_recent(limit)

    def delete_job(self, job_id: str) -> None:
        self.jobs.delete(job_id)
