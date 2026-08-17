"""Relationship edges: one ArangoDB edge document per resolved connection between two entities.

This is a real edge collection — every document carries `_from`/`_to` handles into the entities
collection alongside the denormalized `source`/`target` names the templates render. The
denormalized names are what the flat list views use; the handles are what make `neighbors()` and
any future multi-hop question (blast radius, upstream dependencies, cycle detection) a database
traversal instead of a full scan plus an in-memory graph build.
"""

from __future__ import annotations

from arango.database import StandardDatabase

from pyro.db.keys import entity_key, now_iso, relationship_key


class RelationshipRepository:
    def __init__(
        self, db: StandardDatabase, collection: str, entities_collection: str
    ) -> None:
        self._db = db
        self._name = collection
        self._entities = entities_collection
        self._col = db.collection(collection)

    def _query(self, aql: str, **bind: object) -> list:
        return list(self._db.aql.execute(aql, bind_vars={"@col": self._name, **bind}))

    def upsert(
        self,
        company_name: str,
        source: str,
        target: str,
        relation: str,
        as_of: str | None,
        source_article_id: str | None,
        relation_phrase: str | None = None,
    ) -> str:
        """Upsert one edge, keyed by (company, source, relation, target) so re-merging the same
        article's relationships overwrites rather than duplicates. `relation` is expected to be a
        canonical predicate (extract.schema.RelationKind); `relation_phrase` carries the model's
        original wording when it differed."""
        key = relationship_key(company_name, source, relation, target)
        source_key = entity_key(company_name, source)
        target_key = entity_key(company_name, target)
        doc = {
            "_key": key,
            "_from": f"{self._entities}/{source_key}",
            "_to": f"{self._entities}/{target_key}",
            "company_name": company_name,
            "source": source,
            "source_key": source_key,
            "target": target,
            "target_key": target_key,
            "relation": relation,
            "relation_phrase": relation_phrase,
            "as_of": as_of,
            "source_article_id": source_article_id,
            "updated_at": now_iso(),
        }
        # insert(overwrite=True) rather than has()-then-update: one round trip instead of two, and
        # no window between the check and the write.
        self._col.insert(doc, overwrite=True, silent=True)
        return key

    def list_all(self, company_name: str) -> list[dict]:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          RETURN doc
        """
        return self._query(query, company_name=company_name)

    def neighbors(
        self,
        company_name: str,
        name: str,
        depth: int = 1,
        direction: str = "ANY",
    ) -> list[dict]:
        """Entities reachable from `name` within `depth` hops — a native AQL traversal over the
        edge collection, so the whole graph never has to be loaded to answer it.

        `direction` is one of OUTBOUND (what this depends on), INBOUND (what depends on this) or
        ANY. Validated against a literal set because AQL takes the traversal direction as a
        keyword, not a bind parameter, so it is interpolated into the query text.
        """
        if direction not in ("OUTBOUND", "INBOUND", "ANY"):
            raise ValueError(f"unknown direction: {direction}")
        start = f"{self._entities}/{entity_key(company_name, name)}"
        query = f"""
        FOR vertex, edge IN 1..@depth {direction} @start @@col
          OPTIONS {{ uniqueVertices: "global", bfs: true }}
          RETURN DISTINCT {{ entity: vertex, via: edge.relation }}
        """
        return self._query(query, start=start, depth=int(depth))

    def delete_key(self, key: str) -> None:
        self._col.delete(key, ignore_missing=True)

    def delete_for_company(self, company_name: str) -> None:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          REMOVE doc IN @@col
        """
        self._query(query, company_name=company_name)
