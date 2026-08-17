"""ArangoDB storage for pipeline state (raw/cleaned/extracted articles) and for the
company-wide entity/relationship graph merged from them.

Three collections in a single ArangoDB database, all scoped by `company_name` so one
database serves every company:
  - articles_collection: one document per scraped article (pipeline state).
  - entities_collection: one document per resolved system/service/datastore/etc. the graph-merge
    pass has decided is distinct, company-scoped by key prefix (see _slug/entity keys below).
  - relationships_collection: one document per resolved edge between two entities.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from arango import ArangoClient

if TYPE_CHECKING:
    from pyro.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class Article:
    id: str
    source_url: str
    company_name: str
    title: str | None = None
    raw_html: str | None = None
    cleaned_text: str | None = None
    extracted_graph: dict[str, Any] | None = None
    scraped_at: str | None = None
    extracted_at: str | None = None
    # Set once this article's extracted entities/relationships have been folded into the
    # company's graph (see graph.merge.run_graph_merge) — lets a merge run process only new
    # articles instead of replaying every article through the merge prompt on every run.
    graph_merged_at: str | None = None

    @classmethod
    def from_doc(cls, doc: dict) -> Article:
        return cls(
            id=doc["_key"],
            source_url=doc["source_url"],
            title=doc.get("title"),
            company_name=doc["company_name"],
            raw_html=doc.get("raw_html"),
            cleaned_text=doc.get("cleaned_text"),
            extracted_graph=doc.get("extracted_graph"),
            scraped_at=doc.get("scraped_at"),
            extracted_at=doc.get("extracted_at"),
            graph_merged_at=doc.get("graph_merged_at"),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "x"


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
        client = ArangoClient(hosts=host)
        sys_db = client.db("_system", username=username, password=password)
        if not sys_db.has_database(database):
            sys_db.create_database(database)
        self._db = client.db(database, username=username, password=password)

        if not self._db.has_collection(articles_collection):
            self._db.create_collection(articles_collection)
        self._articles = self._db.collection(articles_collection)
        # Every read path below filters (and often sorts) on these — fetch_extracted,
        # fetch_pending_merge, list_articles, list_companies_with_pending_merge. ArangoDB's
        # index-create API is idempotent on identical type+fields, so re-running this on every
        # connect (existing deployments included) is safe, same as the has_collection guards.
        self._articles.add_index(
            {
                "type": "persistent",
                "fields": ["company_name"],
                "name": "idx_articles_company_name",
            }
        )
        self._articles.add_index(
            {
                "type": "persistent",
                "fields": ["company_name", "extracted_at"],
                "name": "idx_articles_company_name_extracted_at",
            }
        )
        self._articles.add_index(
            {
                "type": "persistent",
                "fields": ["company_name", "graph_merged_at"],
                "name": "idx_articles_company_name_graph_merged_at",
            }
        )

        if not self._db.has_collection(entities_collection):
            self._db.create_collection(entities_collection)
        self._entities = self._db.collection(entities_collection)
        self._entities.add_index(
            {
                "type": "persistent",
                "fields": ["company_name"],
                "name": "idx_entities_company_name",
            }
        )

        if not self._db.has_collection(relationships_collection):
            self._db.create_collection(relationships_collection)
        self._relationships = self._db.collection(relationships_collection)
        self._relationships.add_index(
            {
                "type": "persistent",
                "fields": ["company_name"],
                "name": "idx_relationships_company_name",
            }
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- articles: raw scrape -> clean -> extract pipeline state ---

    def upsert_raw(
        self,
        id: str,
        source_url: str,
        title: str | None,
        company_name: str,
        raw_html: str,
    ) -> None:
        """Insert a newly scraped article. No-ops if the id already exists (dedup)."""
        if self._articles.has(id):
            return
        self._articles.insert(
            {
                "_key": id,
                "source_url": source_url,
                "title": title,
                "company_name": company_name,
                "raw_html": raw_html,
                "cleaned_text": None,
                "extracted_graph": None,
                "scraped_at": _now(),
                "extracted_at": None,
                "graph_merged_at": None,
            }
        )

    def exists(self, id: str) -> bool:
        return self._articles.has(id)

    def mark_cleaned(self, id: str, cleaned_text: str) -> None:
        self._articles.update({"_key": id, "cleaned_text": cleaned_text})

    def mark_extracted(self, id: str, extracted_graph: dict[str, Any]) -> None:
        self._articles.update(
            {
                "_key": id,
                "extracted_graph": extracted_graph,
                "extracted_at": _now(),
            }
        )

    def mark_graph_merged(self, id: str) -> None:
        """Record that an article's extracted entities/relationships have been folded into the
        company's graph, so run_graph_merge skips it on future runs instead of re-merging it."""
        self._articles.update({"_key": id, "graph_merged_at": _now()})

    def fetch_unprocessed(self, stage: str, limit: int | None = None) -> list[Article]:
        """stage: 'clean' (raw_html set, cleaned_text null) or
        'extract' (cleaned_text set, extracted_at null)."""
        if stage == "clean":
            filter_clause = "FILTER doc.raw_html != null AND doc.cleaned_text == null"
        elif stage == "extract":
            filter_clause = (
                "FILTER doc.cleaned_text != null AND doc.extracted_at == null"
            )
        else:
            raise ValueError(f"unknown stage: {stage}")
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        query = f"FOR doc IN {self._articles.name} {filter_clause} {limit_clause} RETURN doc"
        cursor = self._db.aql.execute(query)
        return [Article.from_doc(d) for d in cursor]

    def fetch_extracted(self, company_name: str) -> list[Article]:
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name AND doc.extracted_at != null
          SORT doc.extracted_at
          RETURN doc
        """
        cursor = self._db.aql.execute(query, bind_vars={"company_name": company_name})
        return [Article.from_doc(d) for d in cursor]

    def fetch_pending_merge(self, company_name: str) -> list[Article]:
        """Extracted articles for company_name not yet folded into the graph, oldest first (so
        a merge run processes articles in the order they were extracted)."""
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name
            AND doc.extracted_at != null
            AND doc.graph_merged_at == null
          SORT doc.extracted_at
          RETURN doc
        """
        cursor = self._db.aql.execute(query, bind_vars={"company_name": company_name})
        return [Article.from_doc(d) for d in cursor]

    def list_articles(self, company_name: str) -> list[Article]:
        """All articles for a company regardless of pipeline stage, newest scrape first.
        Used by the dashboard's live extraction view."""
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name
          SORT doc.scraped_at DESC
          RETURN doc
        """
        cursor = self._db.aql.execute(query, bind_vars={"company_name": company_name})
        return [Article.from_doc(d) for d in cursor]

    def get_article_for_company(
        self, company_name: str, article_id: str
    ) -> Article | None:
        """Article ids are unique on their own, but this also checks company ownership so one
        company's dashboard view can't fetch another's article by key."""
        doc = self._articles.get(article_id)
        if doc is None or doc.get("company_name") != company_name:
            return None
        return Article.from_doc(doc)

    def delete_article(self, article_id: str) -> None:
        self._articles.delete(article_id, ignore_missing=True)

    def delete_articles_for_company(self, company_name: str) -> None:
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name
          REMOVE doc IN {self._articles.name}
        """
        self._db.aql.execute(query, bind_vars={"company_name": company_name})

    def list_companies_with_pending_merge(self) -> list[str]:
        """Distinct company_names with at least one extracted article that hasn't been folded
        into the graph yet, for a cron job to pick up instead of a person clicking "Run merge".
        """
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.extracted_at != null AND doc.graph_merged_at == null
          RETURN DISTINCT doc.company_name
        """
        return sorted(self._db.aql.execute(query))

    def list_company_names(self) -> list[str]:
        """Distinct company names seen across both collections, for the dashboard's
        company picker."""
        query = f"""
        FOR name IN UNION_DISTINCT(
          (FOR doc IN {self._articles.name} RETURN doc.company_name),
          (FOR doc IN {self._entities.name} RETURN doc.company_name)
        )
        SORT name
        RETURN name
        """
        return list(self._db.aql.execute(query))

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
        """Create the entity if new, or fold `alias` into an existing one — the graph-merge pass
        has already decided whether `name` is a brand-new entity or the canonical name of one
        that already exists; this just persists that decision idempotently. Returns the
        entity's key. Keyed by company + normalized name so the same system merged from two
        different articles always resolves to the same document."""
        key = f"{_slug(company_name)}--{_slug(name)}"
        existing = self._entities.get(key)
        if existing is None:
            self._entities.insert(
                {
                    "_key": key,
                    "company_name": company_name,
                    "name": name,
                    "kind": kind,
                    "domain": domain,
                    "aliases": [alias] if alias and alias != name else [],
                    "first_seen_article_id": first_seen_article_id,
                    "updated_at": _now(),
                }
            )
        else:
            aliases = set(existing.get("aliases") or [])
            if alias and alias != name:
                aliases.add(alias)
            self._entities.update(
                {"_key": key, "aliases": sorted(aliases), "updated_at": _now()}
            )
        return key

    def upsert_relationship(
        self,
        company_name: str,
        source: str,
        target: str,
        relation: str,
        as_of: str | None,
        source_article_id: str | None,
    ) -> None:
        """Upsert one edge, keyed by (company, source, relation, target) so re-merging the same
        article's relationships (e.g. a re-run) overwrites rather than duplicates."""
        key = f"{_slug(company_name)}--{_slug(source)}--{_slug(relation)}--{_slug(target)}"
        doc = {
            "_key": key,
            "company_name": company_name,
            "source": source,
            "source_key": f"{_slug(company_name)}--{_slug(source)}",
            "target": target,
            "target_key": f"{_slug(company_name)}--{_slug(target)}",
            "relation": relation,
            "as_of": as_of,
            "source_article_id": source_article_id,
            "updated_at": _now(),
        }
        if self._relationships.has(key):
            self._relationships.update(doc)
        else:
            self._relationships.insert(doc)

    def list_entity_names(self, company_name: str) -> list[str]:
        """Flat list of canonical entity names for company_name — the cheap context the
        graph-merge prompt is shown to decide reuse-vs-new, instead of full entity records
        (see docs/architecture.md)."""
        query = f"""
        FOR doc IN {self._entities.name}
          FILTER doc.company_name == @company_name
          SORT doc.name
          RETURN doc.name
        """
        return list(self._db.aql.execute(query, bind_vars={"company_name": company_name}))

    def list_entities(self, company_name: str) -> list[dict]:
        query = f"""
        FOR doc IN {self._entities.name}
          FILTER doc.company_name == @company_name
          SORT doc.name
          RETURN doc
        """
        return list(self._db.aql.execute(query, bind_vars={"company_name": company_name}))

    def list_relationships(self, company_name: str) -> list[dict]:
        query = f"""
        FOR doc IN {self._relationships.name}
          FILTER doc.company_name == @company_name
          RETURN doc
        """
        return list(self._db.aql.execute(query, bind_vars={"company_name": company_name}))

    def delete_graph_for_company(self, company_name: str) -> None:
        """Deletes all entities/relationships for company_name and resets every one of its
        articles' graph_merged_at, so a subsequent merge run treats them as new and rebuilds
        from scratch — the supported way to force a full graph rebuild (e.g. after changing the
        merge prompt), since run_graph_merge itself only processes not-yet-merged articles."""
        reset_query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name AND doc.graph_merged_at != null
          UPDATE doc WITH {{ graph_merged_at: null }} IN {self._articles.name}
        """
        self._db.aql.execute(reset_query, bind_vars={"company_name": company_name})
        for collection in (self._relationships, self._entities):
            query = f"""
            FOR doc IN {collection.name}
              FILTER doc.company_name == @company_name
              REMOVE doc IN {collection.name}
            """
            self._db.aql.execute(query, bind_vars={"company_name": company_name})


@contextmanager
def open_db(
    host: str = "http://localhost:8529",
    database: str = "pyro",
    username: str = "root",
    password: str = "",
    articles_collection: str = "articles",
    entities_collection: str = "entities",
    relationships_collection: str = "relationships",
) -> Iterator[Database]:
    db = Database(
        host=host,
        database=database,
        username=username,
        password=password,
        articles_collection=articles_collection,
        entities_collection=entities_collection,
        relationships_collection=relationships_collection,
    )
    try:
        yield db
    finally:
        db.close()


@contextmanager
def open_db_from_settings(settings: Settings) -> Iterator[Database]:
    """Same as open_db, but reads connection params off a pyro.config.Settings instance."""
    with open_db(
        host=settings.arango.host,
        database=settings.arango.database,
        username=settings.arango_username,
        password=settings.arango_password or "",
        articles_collection=settings.arango.articles_collection,
        entities_collection=settings.arango.entities_collection,
        relationships_collection=settings.arango.relationships_collection,
    ) as db:
        yield db
