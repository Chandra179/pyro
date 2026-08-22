"""Relationship edges: one ArangoDB edge document per resolved connection between two entities.

Carries `_from`/`_to` handles into entities (for AQL traversal) alongside denormalized
`source`/`target` names (for the flat list views templates render).
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
        extra_source_article_ids: list[str] | None = None,
    ) -> str:
        """Upsert one edge, keyed by (company, source, relation, target) so re-merging updates
        rather than duplicates.

        `source_article_ids` accumulates every article that has stated this edge rather than being
        overwritten — an edge confirmed by five posts should stay distinguishable from one
        mentioned once. `extra_source_article_ids` lets graph/backfill.py carry an old edge's full
        history into a rewritten key instead of collapsing it to one id."""
        key = relationship_key(company_name, source, relation, target)
        source_key = entity_key(company_name, source)
        target_key = entity_key(company_name, target)
        new_ids = list(dict.fromkeys(extra_source_article_ids or []))
        if source_article_id and source_article_id not in new_ids:
            new_ids.append(source_article_id)

        query = """
        UPSERT { _key: @key }
        INSERT {
          _key: @key,
          _from: @from_handle,
          _to: @to_handle,
          company_name: @company_name,
          source: @source,
          source_key: @source_key,
          target: @target,
          target_key: @target_key,
          relation: @relation,
          relation_phrase: @relation_phrase,
          as_of: @as_of,
          source_article_ids: @new_ids,
          invalid_at: null,
          updated_at: @updated_at
        }
        UPDATE {
          source: @source,
          source_key: @source_key,
          target: @target,
          target_key: @target_key,
          relation: @relation,
          relation_phrase: @relation_phrase,
          as_of: @as_of,
          source_article_ids: APPEND(
            OLD.source_article_ids != null
              ? OLD.source_article_ids
              : (OLD.source_article_id != null ? [OLD.source_article_id] : []),
            @new_ids,
            true
          ),
          updated_at: @updated_at
        }
        IN @@col
        """
        # UPDATE deliberately never mentions invalid_at (AQL merges only listed attributes) — a
        # routine re-merge must not reopen a validity window invalidate_outgoing closed.
        self._query(
            query,
            key=key,
            from_handle=f"{self._entities}/{source_key}",
            to_handle=f"{self._entities}/{target_key}",
            company_name=company_name,
            source=source,
            source_key=source_key,
            target=target,
            target_key=target_key,
            relation=relation,
            relation_phrase=relation_phrase,
            as_of=as_of,
            new_ids=new_ids,
            updated_at=now_iso(),
        )
        return key

    def invalidate_outgoing(
        self,
        company_name: str,
        source: str,
        at: str,
        exclude_relation: str | None = None,
    ) -> int:
        """Close the validity window on every open edge sourced from `source` (except
        `exclude_relation`) — used when a `replaced_by` edge marks `source` decommissioned.
        Edges pointing *at* `source` are left untouched since those remain historically true.
        Idempotent. Returns how many edges this call closed."""
        source_key = entity_key(company_name, source)
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          FILTER doc.source_key == @source_key
          FILTER doc.invalid_at == null
          FILTER @exclude_relation == null OR doc.relation != @exclude_relation
          UPDATE doc WITH { invalid_at: @at } IN @@col
          RETURN doc._key
        """
        return len(
            self._query(
                query,
                company_name=company_name,
                source_key=source_key,
                at=at,
                exclude_relation=exclude_relation,
            )
        )

    def list_all(self, company_name: str) -> list[dict]:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          RETURN doc
        """
        return self._query(query, company_name=company_name)

    def delete_key(self, key: str) -> None:
        self._col.delete(key, ignore_missing=True)

    def delete_for_company(self, company_name: str) -> None:
        query = """
        FOR doc IN @@col
          FILTER doc.company_name == @company_name
          REMOVE doc IN @@col
        """
        self._query(query, company_name=company_name)
