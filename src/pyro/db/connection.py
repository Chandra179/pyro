"""Connecting to ArangoDB and bringing the schema up to date.

Bootstrapping (has_database/has_collection/add_index) is guarded to run once per process, not
once per connection — the dashboard opens one connection per read accessor, and a naive version
of this cost a full bootstrap on every poll.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from arango import ArangoClient
from arango.database import StandardDatabase

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionParams:
    """Identifies one physical connection + schema; frozen/hashable to key the caches below."""

    host: str
    database: str
    username: str
    password: str
    articles_collection: str
    entities_collection: str
    relationships_collection: str
    jobs_collection: str
    merge_locks_collection: str


# ArangoDB's index-create API is idempotent for an identical type+fields+name, so re-running the
# whole set on every bootstrap is safe.
_ARTICLE_INDEXES = [
    {"type": "persistent", "fields": ["company_name"], "name": "idx_articles_company_name"},
    {
        "type": "persistent",
        "fields": ["company_name", "extracted_at"],
        "name": "idx_articles_company_name_extracted_at",
    },
    {
        "type": "persistent",
        "fields": ["company_name", "graph_merged_at"],
        "name": "idx_articles_company_name_graph_merged_at",
    },
    # Backs fetch_unprocessed's stage filters.
    {
        "type": "persistent",
        "fields": ["company_name", "cleaned_text"],
        "name": "idx_articles_company_name_cleaned_text",
    },
    # Backs list_summaries' paginated newest-first read.
    {
        "type": "persistent",
        "fields": ["company_name", "scraped_at"],
        "name": "idx_articles_company_name_scraped_at",
    },
]

_ENTITY_INDEXES = [
    {"type": "persistent", "fields": ["company_name"], "name": "idx_entities_company_name"},
]

_RELATIONSHIP_INDEXES = [
    {
        "type": "persistent",
        "fields": ["company_name"],
        "name": "idx_relationships_company_name",
    },
    # Backs invalidate_outgoing (graph/merge.py, on a `replaced_by` edge).
    {
        "type": "persistent",
        "fields": ["company_name", "source_key"],
        "name": "idx_relationships_company_name_source_key",
    },
]

_JOB_INDEXES = [
    {"type": "persistent", "fields": ["created_at"], "name": "idx_jobs_created_at"},
]

# expireAfter=0: `expires_at` already stores the absolute expiry time (Unix seconds), not an
# offset from document creation — see db/merge_locks.py.
_MERGE_LOCK_INDEXES = [
    {
        "type": "ttl",
        "fields": ["expires_at"],
        "expireAfter": 0,
        "name": "idx_merge_locks_expires_at",
    },
]

_MIGRATION_HINT = (
    "Collection {name!r} exists as a document collection, but relationships are now stored as "
    "graph edges (_from/_to) so they can be traversed with AQL. Recreate it as an edge "
    "collection (drop and let it be re-bootstrapped, migrating any existing data by hand first)."
)

# Guards the caches below against concurrent bootstrap from job/request threads racing for a connection.
_lock = threading.Lock()
_clients: dict[str, ArangoClient] = {}
_databases: dict[ConnectionParams, StandardDatabase] = {}
_bootstrapped: set[ConnectionParams] = set()


def _client_for(host: str) -> ArangoClient:
    client = _clients.get(host)
    if client is None:
        client = ArangoClient(hosts=host)
        _clients[host] = client
    return client


def _ensure_collection(db: StandardDatabase, name: str, *, edge: bool, indexes: list[dict]):
    if not db.has_collection(name):
        db.create_collection(name, edge=edge)
    collection = db.collection(name)
    if edge and not collection.properties().get("edge", False):
        raise RuntimeError(_MIGRATION_HINT.format(name=name))
    for index in indexes:
        collection.add_index(index)
    return collection


def _bootstrap(db: StandardDatabase, params: ConnectionParams) -> None:
    _ensure_collection(
        db, params.articles_collection, edge=False, indexes=_ARTICLE_INDEXES
    )
    _ensure_collection(
        db, params.entities_collection, edge=False, indexes=_ENTITY_INDEXES
    )
    # Edge collection so relationships (`_from`/`_to`) are AQL-traversable. ArangoDB doesn't
    # enforce that endpoints exist — a dangling edge just yields nothing when traversed, which is
    # fine since LLM output occasionally names an endpoint it didn't also list as an entity.
    _ensure_collection(
        db, params.relationships_collection, edge=True, indexes=_RELATIONSHIP_INDEXES
    )
    _ensure_collection(db, params.jobs_collection, edge=False, indexes=_JOB_INDEXES)
    _ensure_collection(
        db, params.merge_locks_collection, edge=False, indexes=_MERGE_LOCK_INDEXES
    )


def connect(params: ConnectionParams) -> StandardDatabase:
    """Return the process-wide connection for `params`, bootstrapping the schema on first use."""
    with _lock:
        existing = _databases.get(params)
        if existing is not None:
            return existing

        client = _client_for(params.host)
        sys_db = client.db("_system", username=params.username, password=params.password)
        if not sys_db.has_database(params.database):
            sys_db.create_database(params.database)
        db = client.db(
            params.database, username=params.username, password=params.password
        )
        if params not in _bootstrapped:
            _bootstrap(db, params)
            _bootstrapped.add(params)
        _databases[params] = db
        return db


def reset_cache() -> None:
    """Drop cached connections — for tests and credential changes mid-process."""
    with _lock:
        _databases.clear()
        _bootstrapped.clear()
        _clients.clear()
