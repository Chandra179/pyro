"""Entity documents: one per resolved system/service/datastore the graph-merge pass has decided
is distinct, company-scoped via the key prefix (see db.keys.entity_key)."""

from __future__ import annotations

from arango.database import StandardDatabase

from pyro.db.keys import entity_key, now_iso


class EntityRepository:
    def __init__(self, db: StandardDatabase, collection: str) -> None:
        self._db = db
        self._name = collection
        self._col = db.collection(collection)

    def _query(self, aql: str, **bind: object) -> list:
        return list(self._db.aql.execute(aql, bind_vars={"@col": self._name, **bind}))

    def upsert(
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
        that already exists; this just persists that decision idempotently. Returns the entity's
        key."""
        key = entity_key(company_name, name)
        existing = self._col.get(key)
        if existing is None:
            self._col.insert(
                {
                    "_key": key,
                    "company_name": company_name,
                    "name": name,
                    "kind": kind,
                    "domain": domain,
                    "aliases": [alias] if alias and alias != name else [],
                    "first_seen_article_id": first_seen_article_id,
                    "updated_at": now_iso(),
                }
            )
        else:
            aliases = set(existing.get("aliases") or [])
            if alias and alias != name:
                aliases.add(alias)
            self._col.update(
                {"_key": key, "aliases": sorted(aliases), "updated_at": now_iso()}
            )
        return key

    def list_names(self, company_name: str) -> list[str]:
        """Flat list of canonical entity names for company_name — the cheap context the
        graph-merge pass matches against (see graph/resolve.py) before deciding what, if
        anything, still needs a model call."""
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          SORT doc.name
          RETURN doc.name
        """
        return self._query(query, company_name=company_name)

    def list_all(self, company_name: str) -> list[dict]:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          SORT doc.name
          RETURN doc
        """
        return self._query(query, company_name=company_name)

    def list_company_names(self) -> list[str]:
        query = """
        FOR doc IN @@col
          RETURN DISTINCT doc.company_name
        """
        return self._query(query)

    def delete_for_company(self, company_name: str) -> None:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          REMOVE doc IN @@col
        """
        self._query(query, company_name=company_name)
