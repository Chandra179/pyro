"""`Database` — the facade every caller talks to.

It owns no query logic of its own; it composes the three repositories and exposes their methods
under the names the rest of the codebase already uses. The split exists because the article
pipeline and the entity graph are separate concerns that happened to share a file: the graph side
is the one growing traversals, and it no longer drags the scrape/clean/extract state along with
it. The facade is what keeps that split from rippling into every call site.
"""

from __future__ import annotations

from typing import Any, Self

from pyro.db.articles import ArticleRepository
from pyro.db.connection import ConnectionParams, connect
from pyro.db.entities import EntityRepository
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
    ):
        params = ConnectionParams(
            host=host,
            database=database,
            username=username,
            password=password,
            articles_collection=articles_collection,
            entities_collection=entities_collection,
            relationships_collection=relationships_collection,
        )
        self._db = connect(params)
        self.articles = ArticleRepository(self._db, articles_collection)
        self.entities = EntityRepository(self._db, entities_collection)
        self.relationships = RelationshipRepository(
            self._db, relationships_collection, entities_collection
        )

    # The underlying connection is process-wide and pooled (see db.connection), so an individual
    # Database has no resources to release. Kept so `with open_db(...)` reads naturally and so a
    # future connection-per-caller model has somewhere to hook.
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

    def get_article_for_company(self, company_name: str, article_id: str) -> Article | None:
        return self.articles.get_for_company(company_name, article_id)

    def delete_article(self, article_id: str) -> None:
        self.articles.delete(article_id)

    def delete_articles_for_company(self, company_name: str) -> None:
        self.articles.delete_for_company(company_name)

    def list_companies_with_pending_merge(self) -> list[str]:
        return self.articles.list_companies_with_pending_merge()

    def list_company_names(self) -> list[str]:
        """Distinct company names seen across articles and entities, for the dashboard's company
        picker. Unioned in Python rather than with AQL's UNION_DISTINCT so each side uses its own
        company_name index instead of the server materializing both full scans first."""
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
    ) -> str:
        return self.entities.upsert(
            company_name, name, kind, domain, alias, first_seen_article_id
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
    ) -> None:
        self.relationships.upsert(
            company_name,
            source,
            target,
            relation,
            as_of,
            source_article_id,
            relation_phrase=relation_phrase,
        )

    def list_entity_names(self, company_name: str) -> list[str]:
        return self.entities.list_names(company_name)

    def list_entities(self, company_name: str) -> list[dict]:
        return self.entities.list_all(company_name)

    def list_relationships(self, company_name: str) -> list[dict]:
        return self.relationships.list_all(company_name)

    def neighbors(
        self, company_name: str, name: str, depth: int = 1, direction: str = "ANY"
    ) -> list[dict]:
        """Graph traversal from one entity — see RelationshipRepository.neighbors."""
        return self.relationships.neighbors(company_name, name, depth, direction)

    def delete_graph_for_company(self, company_name: str) -> None:
        """Deletes all entities/relationships for company_name and resets every one of its
        articles' graph_merged_at, so a subsequent merge run treats them as new and rebuilds
        from scratch — the supported way to force a full graph rebuild (e.g. after changing the
        merge prompt), since run_graph_merge itself only processes not-yet-merged articles."""
        self.articles.reset_graph_merged(company_name)
        self.relationships.delete_for_company(company_name)
        self.entities.delete_for_company(company_name)
