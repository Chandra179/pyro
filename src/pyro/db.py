"""ArangoDB storage for pipeline state (raw/cleaned/extracted articles) and for the
synthesized/routed markdown documents that used to live under output/.

Two collections in a single ArangoDB database, both scoped by `company_name` so one
database serves every company:
  - articles_collection: one document per scraped article (pipeline state).
  - docs_collection: one document per synthesized architecture doc (structured mode:
    one per domain; freeform mode: one per AI-routed topic), replacing output/*.md.
"""

from __future__ import annotations

import logging
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
    extracted_facts: dict[str, Any] | None = None
    scraped_at: str | None = None
    extracted_at: str | None = None
    # Freeform mode only: the docs._key of the topic file this article was last folded into,
    # or None if it hasn't been routed yet. Lets run_freeform_synthesis process only new
    # articles instead of replaying every article through the router on every run.
    routed_doc_key: str | None = None

    @classmethod
    def from_doc(cls, doc: dict) -> Article:
        return cls(
            id=doc["_key"],
            source_url=doc["source_url"],
            title=doc.get("title"),
            company_name=doc["company_name"],
            raw_html=doc.get("raw_html"),
            cleaned_text=doc.get("cleaned_text"),
            extracted_facts=doc.get("extracted_facts"),
            scraped_at=doc.get("scraped_at"),
            extracted_at=doc.get("extracted_at"),
            routed_doc_key=doc.get("routed_doc_key"),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(
        self,
        host: str = "http://localhost:8529",
        database: str = "pyro",
        username: str = "root",
        password: str = "",
        articles_collection: str = "articles",
        docs_collection: str = "docs",
    ):
        client = ArangoClient(hosts=host)
        sys_db = client.db("_system", username=username, password=password)
        if not sys_db.has_database(database):
            sys_db.create_database(database)
        self._db = client.db(database, username=username, password=password)

        if not self._db.has_collection(articles_collection):
            self._db.create_collection(articles_collection)
        self._articles = self._db.collection(articles_collection)

        if not self._db.has_collection(docs_collection):
            self._db.create_collection(docs_collection)
        self._docs = self._db.collection(docs_collection)

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
                "extracted_facts": None,
                "scraped_at": _now(),
                "extracted_at": None,
                "routed_doc_key": None,
            }
        )

    def exists(self, id: str) -> bool:
        return self._articles.has(id)

    def mark_cleaned(self, id: str, cleaned_text: str) -> None:
        self._articles.update({"_key": id, "cleaned_text": cleaned_text})

    def mark_extracted(self, id: str, extracted_facts: dict[str, Any]) -> None:
        self._articles.update(
            {
                "_key": id,
                "extracted_facts": extracted_facts,
                "extracted_at": _now(),
            }
        )

    def mark_routed(self, id: str, doc_key: str) -> None:
        """Freeform mode: record that an article has been folded into docs._key doc_key, so
        run_freeform_synthesis skips it on future runs instead of re-routing it."""
        self._articles.update({"_key": id, "routed_doc_key": doc_key})

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
        """Like get_doc_for_company: article ids are unique on their own, but this also checks
        company ownership so one company's dashboard view can't fetch another's article by key."""
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

    def list_companies_with_pending_synthesis(self) -> list[str]:
        """Freeform mode only: distinct company_names with at least one extracted article that
        hasn't been routed into a doc yet (routed_doc_key == null), for a cron job to pick up
        instead of a person clicking "Run synthesis". Structured mode has no per-article routing
        state — every article always looks "pending" there, since run_structured_synthesis
        always rebuilds from scratch — so a cron job driven by this must be freeform-only, or it
        would re-run full (costly) synthesis for every company on every tick."""
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.extracted_at != null AND doc.routed_doc_key == null
          RETURN DISTINCT doc.company_name
        """
        return sorted(self._db.aql.execute(query))

    def list_company_names(self) -> list[str]:
        """Distinct company names seen across both collections, for the dashboard's
        company picker."""
        query = f"""
        FOR name IN UNION_DISTINCT(
          (FOR doc IN {self._articles.name} RETURN doc.company_name),
          (FOR doc IN {self._docs.name} RETURN doc.company_name)
        )
        SORT name
        RETURN name
        """
        return list(self._db.aql.execute(query))

    # --- docs: synthesized/routed architecture documents (replaces output/*.md) ---

    def upsert_doc(
        self, key: str, company_name: str, content: str, heading: str | None = None
    ) -> None:
        """Create or overwrite a synthesized doc, keyed by its slug (e.g. domain or AI-chosen topic)."""
        doc = {
            "_key": key,
            "company_name": company_name,
            "heading": heading,
            "content": content,
            "updated_at": _now(),
        }
        if self._docs.has(key):
            self._docs.update(doc)
        else:
            self._docs.insert(doc)

    def get_doc_for_company(self, company_name: str, key: str) -> dict | None:
        """Checks company ownership — doc keys (e.g.
        "architecture-authentication") are slugs, not globally unique across
        companies, so a bare key lookup could return another company's doc."""
        doc = self._docs.get(key)
        if doc is None or doc.get("company_name") != company_name:
            return None
        return doc

    def list_docs(self, company_name: str) -> list[dict]:
        """All synthesized docs for a company, sorted by key. Used both to display results
        and, in freeform mode, to build the routing index of existing topics."""
        query = f"""
        FOR doc IN {self._docs.name}
          FILTER doc.company_name == @company_name
          SORT doc._key
          RETURN doc
        """
        cursor = self._db.aql.execute(query, bind_vars={"company_name": company_name})
        return list(cursor)

    def delete_doc(self, key: str, company_name: str) -> None:
        """Deletes the doc and un-routes any articles that were folded into it (freeform mode),
        so a future synthesis run re-routes them instead of treating them as already handled —
        without this, a deleted topic file would never come back."""
        query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name AND doc.routed_doc_key == @key
          UPDATE doc WITH {{ routed_doc_key: null }} IN {self._articles.name}
        """
        self._db.aql.execute(
            query, bind_vars={"company_name": company_name, "key": key}
        )
        self._docs.delete(key, ignore_missing=True)

    def delete_docs_for_company(self, company_name: str) -> None:
        """Deletes all docs for company_name and un-routes every one of its articles (freeform
        mode), so a subsequent synthesis run treats them as new and rebuilds from scratch — this
        is the supported way to force a full freeform rebuild (e.g. after changing the routing
        prompt), since run_freeform_synthesis itself only processes unrouted articles."""
        clear_query = f"""
        FOR doc IN {self._articles.name}
          FILTER doc.company_name == @company_name AND doc.routed_doc_key != null
          UPDATE doc WITH {{ routed_doc_key: null }} IN {self._articles.name}
        """
        self._db.aql.execute(clear_query, bind_vars={"company_name": company_name})
        query = f"""
        FOR doc IN {self._docs.name}
          FILTER doc.company_name == @company_name
          REMOVE doc IN {self._docs.name}
        """
        self._db.aql.execute(query, bind_vars={"company_name": company_name})


@contextmanager
def open_db(
    host: str = "http://localhost:8529",
    database: str = "pyro",
    username: str = "root",
    password: str = "",
    articles_collection: str = "articles",
    docs_collection: str = "docs",
) -> Iterator[Database]:
    db = Database(
        host=host,
        database=database,
        username=username,
        password=password,
        articles_collection=articles_collection,
        docs_collection=docs_collection,
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
        docs_collection=settings.arango.docs_collection,
    ) as db:
        yield db
