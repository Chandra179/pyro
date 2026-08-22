"""Dashboard pipeline-run documents: one per submitted job, so the Runs page and a run's
merge-call transcript survive a dashboard restart.

Written at coarse granularity — per pipeline-stage transition and per finished merge call, never
per streamed token. Not scoped by company_name like articles/entities/relationships — the Runs
page lists every company's jobs together — though each document still carries company_name.
"""

from __future__ import annotations

from arango.database import StandardDatabase


class JobRepository:
    def __init__(self, db: StandardDatabase, collection: str) -> None:
        self._db = db
        self._name = collection
        self._col = db.collection(collection)

    def save(self, doc: dict) -> None:
        """Upsert a job's full current state, keyed on its own id (`doc["_key"]`)."""
        if self._col.has(doc["_key"]):
            self._col.replace(doc)
        else:
            self._col.insert(doc)

    def get(self, job_id: str) -> dict | None:
        return self._col.get(job_id)

    def list_recent(self, limit: int) -> list[dict]:
        query = """
        FOR doc IN @@col
          SORT doc.created_at DESC
          LIMIT @limit
          RETURN doc
        """
        return list(
            self._db.aql.execute(query, bind_vars={"@col": self._name, "limit": limit})
        )

    def delete(self, job_id: str) -> None:
        self._col.delete(job_id, ignore_missing=True)
