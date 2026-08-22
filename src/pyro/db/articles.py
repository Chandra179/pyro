"""Article documents: the scrape -> clean -> extract -> merged pipeline state, one document per
scraped page, scoped by `company_name`."""

from __future__ import annotations

from arango.database import StandardDatabase

from pyro.db.keys import now_iso
from pyro.db.models import Article

# Which columns are null vs set defines "this article still needs stage X". Kept as data so the
# stage names are enumerable and a typo raises rather than silently matching nothing.
_STAGE_FILTERS = {
    "clean": "doc.raw_html != null AND doc.cleaned_text == null",
    "extract": "doc.cleaned_text != null AND doc.extracted_at == null",
}


class ArticleRepository:
    def __init__(self, db: StandardDatabase, collection: str) -> None:
        self._db = db
        self._name = collection
        self._col = db.collection(collection)

    def _query(self, aql: str, **bind: object) -> list:
        return list(self._db.aql.execute(aql, bind_vars={"@col": self._name, **bind}))

    # --- writes ---

    def upsert_raw(
        self,
        id: str,
        source_url: str,
        title: str | None,
        company_name: str,
        raw_html: str,
    ) -> None:
        """Insert a newly scraped article. No-ops if the id already exists (dedup)."""
        if self._col.has(id):
            return
        self._col.insert(
            {
                "_key": id,
                "source_url": source_url,
                "title": title,
                "company_name": company_name,
                "raw_html": raw_html,
                "cleaned_text": None,
                "extracted_graph": None,
                "scraped_at": now_iso(),
                "extracted_at": None,
                "graph_merged_at": None,
            }
        )

    def exists(self, id: str) -> bool:
        return self._col.has(id)

    def mark_cleaned(self, id: str, cleaned_text: str) -> None:
        # raw_html is the largest field and unused once cleaned; clearing it here is safe since
        # the "clean" stage filter's raw_html != null clause only matters while cleaned_text is null.
        self._col.update({"_key": id, "cleaned_text": cleaned_text, "raw_html": None})

    def mark_extracted(self, id: str, extracted_graph: dict) -> None:
        self._col.update(
            {"_key": id, "extracted_graph": extracted_graph, "extracted_at": now_iso()}
        )

    def mark_graph_merged(self, id: str) -> None:
        self._col.update({"_key": id, "graph_merged_at": now_iso()})

    # --- reads ---

    def fetch_unprocessed(
        self,
        stage: str,
        limit: int | None = None,
        company_name: str | None = None,
    ) -> list[Article]:
        """Articles still awaiting `stage` ('clean' or 'extract').

        Pass company_name when running on behalf of one company — otherwise two concurrent
        jobs can pick up each other's articles, since stages match purely on null fields.
        """
        if stage not in _STAGE_FILTERS:
            raise ValueError(f"unknown stage: {stage}")
        clauses = [_STAGE_FILTERS[stage]]
        bind: dict[str, object] = {}
        if company_name is not None:
            clauses.append("doc.company_name == @company_name")
            bind["company_name"] = company_name
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        query = f"""
        FOR doc IN @@col
          FILTER {" AND ".join(clauses)}
          {limit_clause}
          RETURN doc
        """
        return [Article.from_doc(d) for d in self._query(query, **bind)]

    def fetch_extracted(self, company_name: str) -> list[Article]:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name AND doc.extracted_at != null
          SORT doc.extracted_at
          RETURN doc
        """
        return [
            Article.from_doc(d) for d in self._query(query, company_name=company_name)
        ]

    def fetch_pending_merge(self, company_name: str) -> list[Article]:
        """Extracted articles for company_name not yet folded into the graph, oldest first."""
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
            AND doc.extracted_at != null
            AND doc.graph_merged_at == null
          SORT doc.extracted_at
          RETURN doc
        """
        return [
            Article.from_doc(d) for d in self._query(query, company_name=company_name)
        ]

    def list_summaries(
        self, company_name: str, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        """One page of lightweight article rows for the dashboard's extraction table, plus total
        count for pagination. Projects down to what the table renders, skipping `cleaned_text`
        and `extracted_graph` since the panel polls this every 4 seconds."""
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          SORT doc.scraped_at DESC
          LIMIT @offset, @limit
          RETURN {
            id: doc._key,
            title: doc.title,
            source_url: doc.source_url,
            scraped_at: doc.scraped_at,
            stage: doc.graph_merged_at != null
              ? "merged"
              : (doc.extracted_at != null
                ? "extracted"
                : (doc.cleaned_text != null ? "cleaned" : "scraped")),
            entity_count: doc.extracted_graph != null
              ? LENGTH(doc.extracted_graph.entities)
              : null,
          }
        """
        rows = self._query(
            query, company_name=company_name, limit=limit, offset=offset
        )
        count_query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          COLLECT WITH COUNT INTO total
          RETURN total
        """
        total = self._query(count_query, company_name=company_name)
        return rows, (total[0] if total else 0)

    def list_articles(self, company_name: str) -> list[Article]:
        """All articles for a company, newest scrape first. Drops `raw_html` — the largest field,
        never rendered by the polling dashboard view that calls this."""
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          SORT doc.scraped_at DESC
          RETURN UNSET(doc, "raw_html")
        """
        return [
            Article.from_doc(d) for d in self._query(query, company_name=company_name)
        ]

    def get_for_company(self, company_name: str, article_id: str) -> Article | None:
        """Checks company ownership so one company's dashboard view can't fetch another's article."""
        doc = self._col.get(article_id)
        if doc is None or doc.get("company_name") != company_name:
            return None
        return Article.from_doc(doc)

    def list_companies_with_pending_merge(self) -> list[str]:
        """Distinct company_names with at least one extracted, unmerged article."""
        query = """
        FOR doc IN @@col
          FILTER doc.extracted_at != null AND doc.graph_merged_at == null
          RETURN DISTINCT doc.company_name
        """
        return sorted(self._query(query))

    def list_company_names(self) -> list[str]:
        query = """
        FOR doc IN @@col
          RETURN DISTINCT doc.company_name
        """
        return self._query(query)

    # --- deletes ---

    def delete(self, article_id: str) -> None:
        self._col.delete(article_id, ignore_missing=True)

    def delete_for_company(self, company_name: str) -> None:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          REMOVE doc IN @@col
        """
        self._query(query, company_name=company_name)

    def reset_graph_merged(self, company_name: str) -> None:
        """Clear graph_merged_at so the next merge run treats every extracted article as new."""
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name AND doc.graph_merged_at != null
          UPDATE doc WITH { graph_merged_at: null } IN @@col
        """
        self._query(query, company_name=company_name)
