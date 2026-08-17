"""ArangoDB storage for pipeline state (raw/cleaned/extracted articles) and for the
company-wide entity/relationship graph merged from them.

Three collections in a single ArangoDB database, all scoped by `company_name` so one database
serves every company:
  - articles (document): one per scraped article — see db/articles.py.
  - entities (document): one per resolved system/service/datastore — see db/entities.py.
  - relationships (**edge**): one per resolved connection between two entities, carrying
    `_from`/`_to` handles into `entities` so the graph is traversable in AQL — see
    db/relationships.py.

`Database` (db/database.py) is the facade over all three; import it from here rather than from
the submodule, so the internal layout stays free to move.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from pyro.db.connection import (
    ConnectionParams,
    reset_cache,
)
from pyro.db.database import Database
from pyro.db.keys import entity_key, relationship_key, slug
from pyro.db.models import Article

if TYPE_CHECKING:
    from pyro.config import Settings

__all__ = [
    "Article",
    "ConnectionParams",
    "Database",
    "connection_params_from_settings",
    "entity_key",
    "open_db",
    "open_db_from_settings",
    "relationship_key",
    "reset_cache",
    "slug",
]


def connection_params_from_settings(settings: Settings) -> ConnectionParams:
    return ConnectionParams(
        host=settings.arango.host,
        database=settings.arango.database,
        username=settings.arango_username,
        password=settings.arango_password or "",
        articles_collection=settings.arango.articles_collection,
        entities_collection=settings.arango.entities_collection,
        relationships_collection=settings.arango.relationships_collection,
    )


@contextmanager
def open_db(
    host: str = "http://localhost:8529",
    database: str = "pyro",
    username: str = "root",
    password: str = "",
    articles_collection: str = "articles",
    entities_collection: str = "entities",
    relationships_collection: str = "relationships",
) -> Iterator[Database]:
    db = Database(
        host=host,
        database=database,
        username=username,
        password=password,
        articles_collection=articles_collection,
        entities_collection=entities_collection,
        relationships_collection=relationships_collection,
    )
    try:
        yield db
    finally:
        db.close()


@contextmanager
def open_db_from_settings(settings: Settings) -> Iterator[Database]:
    """Same as open_db, but reads connection params off a pyro.config.Settings instance."""
    params = connection_params_from_settings(settings)
    with open_db(
        host=params.host,
        database=params.database,
        username=params.username,
        password=params.password,
        articles_collection=params.articles_collection,
        entities_collection=params.entities_collection,
        relationships_collection=params.relationships_collection,
    ) as db:
        yield db
