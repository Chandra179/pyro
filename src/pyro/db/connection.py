"""Connecting to ArangoDB and bringing the schema up to date.

Two things live here that used to be inlined in `Database.__init__`:

1. The schema itself (which collections exist, which are edge collections, which indexes back
   which query) as declarative data rather than a sequence of imperative calls.
2. A guard so that work happens *once per process* instead of once per connection. The old code
   ran `has_database` + 3x `has_collection` + 5x `add_index` on every `open_db()`, and the
   dashboard opened one connection per read accessor — so a single `/data/panel` request, which
   polls every 4 seconds, cost three full bootstraps and fifteen index round-trips before it read
   a byte of data.
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
    """Everything that identifies one physical connection + schema. Frozen and hashable so it can
    key the process-wide caches below."""

    host: str
    database: str
    username: str
    password: str
    articles_collection: str
    entities_collection: str
    relationships_collection: str


# Persistent indexes, per collection. Every read path in the repositories filters (and usually
# sorts) on one of these; ArangoDB's index-create API is idempotent for an identical
# type+fields+name, so re-running the whole set is safe on existing deployments.
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
    # Backs fetch_unprocessed's stage filters, which previously had no index at all and fell back
    # to a full collection scan on every clean/extract run.
    {
        "type": "persistent",
        "fields": ["company_name", "cleaned_text"],
        "name": "idx_articles_company_name_cleaned_text",
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
    # Backs invalidate_outgoing (graph/merge.py, on a `replaced_by` edge): closing the validity
    # window on every edge sourced from a decommissioned entity, without a full company-wide scan.
    {
        "type": "persistent",
        "fields": ["company_name", "source_key"],
        "name": "idx_relationships_company_name_source_key",
    },
]

_MIGRATION_HINT = (
    "Collection {name!r} exists as a document collection, but relationships are now stored as "
    "graph edges (_from/_to) so they can be traversed with AQL. Recreate it as an edge "
    "collection (drop and let it be re-bootstrapped, migrating any existing data by hand first)."
)

# Guards the caches below: job threads (api/jobs.py) and request threads can both reach for a
# connection at the same time, and bootstrapping twice concurrently is wasteful, not just untidy.
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
    # Edge collection: relationships carry `_from`/`_to` document handles into the entities
    # collection, which is what makes AQL graph traversals (OUTBOUND/INBOUND/ANY, shortest path,
    # k-hop neighborhoods) possible over the stored graph. Note ArangoDB does not enforce that
    # the referenced documents exist — an edge whose endpoint entity is missing is stored fine and
    # simply yields nothing when traversed, which is the behavior we want given endpoints come
    # from LLM output that occasionally names a system it didn't list as an entity.
    _ensure_collection(
        db, params.relationships_collection, edge=True, indexes=_RELATIONSHIP_INDEXES
    )


def connect(params: ConnectionParams) -> StandardDatabase:
    """Return the process-wide connection for `params`, creating the database and bringing the
    schema up to date the first time it's asked for.

    python-arango's database handle is a thin wrapper over a connection pool and is safe to share,
    so callers get the same object rather than a fresh bootstrap each time.
    """
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
    """Drop cached connections — for tests, and for any caller that has changed credentials
    mid-process."""
    with _lock:
        _databases.clear()
        _bootstrapped.clear()
        _clients.clear()
