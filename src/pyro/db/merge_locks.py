"""Cross-process merge lock.

graph/merge.py used to guard against two merges racing on one company's graph with an in-memory
`threading.Lock` — that only works within one process. cron/merge_pending.sh runs as its own OS
process, entirely separate from the dashboard's uvicorn process (see cron/README.md), so the two
never shared that lock; each held its own, invisible to the other. One document per company_name
here is the thing both processes actually check.

A TTL index on `expires_at` (db/connection.py's _MERGE_LOCK_INDEXES) auto-removes a lock if the
process holding it dies before releasing — a safety net only. The normal path is always an
explicit `release()` in a `finally`.
"""

from __future__ import annotations

import time

from arango.database import StandardDatabase
from arango.exceptions import DocumentInsertError

# ArangoDB's "unique constraint violated" code — the specific failure that means "someone already
# holds this lock." Any other DocumentInsertError (connection trouble, etc.) must not be silently
# read as "locked elsewhere," so it's re-raised instead.
_UNIQUE_CONSTRAINT_VIOLATED = 1210

# Generous relative to any real merge run (sequential per article, an LLM call only for entities
# the deterministic pass can't resolve) — this should only ever fire against a crashed holder, not
# a merge that's simply taking a while.
LOCK_TTL_SECONDS = 60 * 60


class MergeLockRepository:
    def __init__(self, db: StandardDatabase, collection: str) -> None:
        self._col = db.collection(collection)

    def acquire(self, company_name: str) -> bool:
        """True if the lock was free and is now held by this caller; False if another process
        (or another thread in this one) already holds it."""
        now = time.time()
        try:
            self._col.insert(
                {
                    "_key": company_name,
                    "acquired_at": now,
                    "expires_at": now + LOCK_TTL_SECONDS,
                }
            )
            return True
        except DocumentInsertError as exc:
            if exc.error_code == _UNIQUE_CONSTRAINT_VIOLATED:
                return False
            raise

    def release(self, company_name: str) -> None:
        self._col.delete(company_name, ignore_missing=True)
