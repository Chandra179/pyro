"""Backfill for graphs merged before `relation` became a controlled vocabulary.

`canonicalize_relations` rewrites old free-text edges onto extract.schema.RelationKind, using the
same normalizer new extractions go through. Synonym edges converge onto one key and collapse into
one edge; the model's original wording is kept in `relation_phrase`.

One-off: with the vocabulary enforced at extraction time, later merges stay canonical on their own.
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
        # Normalize from the *original* wording so extending the synonym table and re-running
        # can reclassify an edge already folded into the fallback, instead of short-circuiting.
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
            relation_phrase=edge.get("relation_phrase") or old_relation,
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
