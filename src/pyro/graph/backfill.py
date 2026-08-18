"""Backfill for graphs merged before `relation` became a controlled vocabulary.

Edges stored under the old free-text scheme carry whatever the model happened to say — "sends
requests to", "makes downstream calls to", "calls" — and because Database.upsert_relationship keys
on the relation, synonyms describing one connection were stored as several distinct edges and drawn
as several distinct arrows.

`canonicalize_relations` rewrites those edges onto extract.schema.RelationKind, using the same
normalizer new extractions go through. Edges that were only ever synonyms of each other converge on
one key and therefore collapse into one edge — the duplicates disappear rather than being counted
twice. The model's original wording is kept in `relation_phrase`, so nothing is discarded.

This is a one-off: once run, and with the vocabulary enforced at extraction time, later merges stay
canonical on their own.
"""

from __future__ import annotations

import logging

from pyro.db import Database
from pyro.db.keys import relationship_key
from pyro.extract.schema import normalize_relation

logger = logging.getLogger(__name__)


def canonicalize_relations(db: Database, company_name: str) -> dict[str, int]:
    """Rewrite one company's edges onto the canonical vocabulary.

    Returns counts of edges `examined`, `rewritten` (predicate changed) and `collapsed` (rewritten
    onto a key another edge already occupied — i.e. a duplicate that is now gone).
    """
    edges = db.list_relationships(company_name)
    seen_keys = {e["_key"] for e in edges}
    written: set[str] = set()
    rewritten = 0
    collapsed = 0

    for edge in edges:
        stored = edge.get("relation") or ""
        # Normalize from the *original* wording when we have it. That makes this command
        # re-runnable and self-correcting: an edge already folded into `depends_on` by an earlier
        # run keeps its phrase, so extending the synonym table and running again reclassifies it,
        # instead of the run short-circuiting on an already-canonical value.
        original = edge.get("relation_phrase") or stored
        canonical = normalize_relation(original)
        if canonical == stored:
            written.add(edge["_key"])
            continue
        old_relation = original

        new_key = relationship_key(
            company_name, edge["source"], canonical, edge["target"]
        )
        if new_key in written or (new_key in seen_keys and new_key != edge["_key"]):
            collapsed += 1

        db.upsert_relationship(
            company_name,
            edge["source"],
            edge["target"],
            canonical,
            edge.get("as_of"),
            edge.get("source_article_id"),
            # Don't overwrite a phrase that's already there with the same thing twice; otherwise
            # record what the edge used to say so the rewrite stays inspectable.
            relation_phrase=edge.get("relation_phrase") or old_relation,
            # This edge is moving to a new key (its relation changed), so carry over every article
            # that ever confirmed it under the old key instead of collapsing back down to one id.
            extra_source_article_ids=edge.get("source_article_ids"),
        )
        written.add(new_key)
        if new_key != edge["_key"]:
            db.relationships.delete_key(edge["_key"])
        rewritten += 1

    logger.info(
        "%s: canonicalized %d/%d relations (%d duplicates collapsed)",
        company_name,
        rewritten,
        len(edges),
        collapsed,
    )
    return {"examined": len(edges), "rewritten": rewritten, "collapsed": collapsed}
