"""Relationship edges: one ArangoDB edge document per resolved connection between two entities.

This is a real edge collection — every document carries `_from`/`_to` handles into the entities
collection alongside the denormalized `source`/`target` names the templates render. The
denormalized names are what the flat list views use; the handles are what make any future
multi-hop question (blast radius, upstream dependencies, cycle detection) a database traversal
instead of a full scan plus an in-memory graph build.
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
        """Upsert one edge, keyed by (company, source, relation, target) so re-merging the same
        article's relationships updates rather than duplicates. `relation` is expected to be a
        canonical predicate (extract.schema.RelationKind); `relation_phrase` carries the model's
        original wording when it differed.

        `source_article_ids` accumulates every article that has ever stated this edge, rather than
        being overwritten to just the most recent one — otherwise an edge re-confirmed by five
        posts over two years looks identical to one mentioned once in passing, and that
        corroboration signal is unrecoverable once overwritten. `extra_source_article_ids` lets a
        caller (graph/backfill.py, rewriting an edge onto a new key) carry an old edge's full
        history into the new document instead of collapsing it back down to one id."""
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
        # The UPDATE clause deliberately never mentions invalid_at (AQL's UPDATE merges only the
        # attributes listed, leaving others as-is) — a routine re-merge of an edge an article
        # already stated must not silently reopen a validity window invalidate_outgoing closed.
        # Only invalidate_outgoing itself, or a future explicit "revalidate" operation, should ever
        # change invalid_at on an existing edge.
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
        """Close the validity window (set `invalid_at`) on every currently-open edge sourced from
        `source`, except edges whose relation is `exclude_relation`.

        Used when a `replaced_by` edge tells us `source` was decommissioned: the behavior it
        described (`calls`, `writes_to`, ...) stopped being current, so those edges should no
        longer render as part of the live system map — but the `replaced_by` fact itself, and
        anything pointing *at* `source` (e.g. who owned/deployed it), remains historically true and
        is left untouched. Idempotent: edges already closed are skipped. Returns how many edges
        this call closed."""
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
