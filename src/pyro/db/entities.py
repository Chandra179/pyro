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

    def upsert_many(self, company_name: str, items: list[dict]) -> list[str]:
        """Create each new entity, or fold its `alias` into an existing one. Returns the keys, in
        the same order as `items`. Each item: name/kind/domain/alias/first_seen_article_id/
        description/alias_method.

        The first non-empty `description` wins rather than being overwritten on re-merge, since
        later mentions of a known entity are typically less informative than the introducing one.
        Each alias is stored with `alias_method` ("exact"/"fuzzy:<score>"/"llm") as an audit trail
        for why two names were judged the same system — without it a bad merge is unfixable
        without replaying the whole merge history.

        One batch-fetched read + one batched write for the whole call, rather than a `.get()` +
        insert/update round-trip per entity.

        `state` tracks each key's latest computed document as items are processed, not just the
        as-fetched snapshot — two items in the same call resolving to the same canonical name
        (e.g. two raw names both aliasing one existing entity) must still fold onto each other in
        order, the same as calling `upsert` for each in sequence would."""
        if not items:
            return []
        now = now_iso()
        keys = [entity_key(company_name, item["name"]) for item in items]
        state: dict[str, dict] = {
            doc["_key"]: doc
            for doc in self._query(
                "FOR doc IN @@col FILTER doc._key IN @keys RETURN doc", keys=keys
            )
        }

        docs = []
        for item, key in zip(items, keys):
            name = item["name"]
            alias = item.get("alias")
            new_alias = (
                {"name": alias, "method": item.get("alias_method"), "recorded_at": now}
                if alias and alias != name
                else None
            )
            existing = state.get(key)
            if existing is None:
                doc = {
                    "_key": key,
                    "company_name": company_name,
                    "name": name,
                    "kind": item["kind"],
                    "domain": item["domain"],
                    "aliases": [new_alias] if new_alias else [],
                    "first_seen_article_id": item.get("first_seen_article_id"),
                    "description": item.get("description"),
                    "updated_at": now,
                }
                docs.append(doc)
                state[key] = doc
                continue

            # Pre-existing aliases may be plain strings (older schema); normalize in place.
            by_name = {
                a["name"] if isinstance(a, dict) else a: (
                    a if isinstance(a, dict) else {"name": a, "method": None, "recorded_at": None}
                )
                for a in (existing.get("aliases") or [])
            }
            if new_alias:
                by_name[new_alias["name"]] = new_alias
            doc = {
                "_key": key,
                "company_name": existing.get("company_name", company_name),
                "name": existing.get("name", name),
                "kind": existing.get("kind", item["kind"]),
                "domain": existing.get("domain", item["domain"]),
                "aliases": sorted(by_name.values(), key=lambda a: a["name"]),
                "first_seen_article_id": existing.get("first_seen_article_id"),
                # The first non-empty description wins rather than being overwritten on
                # re-merge, since later mentions of a known entity are typically less
                # informative than the introducing one.
                "description": (
                    item.get("description")
                    if item.get("description") and not existing.get("description")
                    else existing.get("description")
                ),
                "updated_at": now,
            }
            docs.append(doc)
            state[key] = doc

        self._query(
            "FOR item IN @items UPSERT { _key: item._key } INSERT item UPDATE item IN @@col",
            items=docs,
        )
        return keys

    def list_names(self, company_name: str) -> list[str]:
        """Canonical entity names for company_name, matched against before falling back to an LLM
        call (see graph/resolve.py)."""
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
