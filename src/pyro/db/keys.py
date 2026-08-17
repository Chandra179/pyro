"""Document-key construction and the timestamp format shared by every collection.

Entity and relationship keys are derived from human names rather than assigned randomly, so the
same system merged from two different articles lands on the same document without a lookup table.
That makes `slug` load-bearing: it defines when two names are "the same" at the storage layer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def slug(text: str) -> str:
    """Lowercase, collapse every run of non-alphanumerics to a single "-", trim.

    Deliberately lossy: "Auth Service", "auth-service" and "auth/service" all collapse to
    `auth-service` and therefore address the same document. That is the intended behavior — it
    absorbs trivial punctuation/casing drift between articles for free, before the merge pass has
    to spend a model call on it — but it does mean two genuinely distinct systems whose names
    differ only in punctuation would collide. In practice company system names don't work that
    way; the assumption is recorded here because it is doing real work in a primary key.

    Never returns "" (falls back to "x") and never emits a doubled "-", which is what lets "--"
    be used safely as a field separator in the composite keys built below.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "x"


def entity_key(company_name: str, name: str) -> str:
    return f"{slug(company_name)}--{slug(name)}"


def relationship_key(
    company_name: str, source: str, relation: str, target: str
) -> str:
    """Keyed on the canonical predicate (see extract.schema.RelationKind), so re-merging an
    article overwrites its edges rather than duplicating them, and so two articles describing the
    same connection in different words converge on one edge instead of two."""
    return "--".join(
        (slug(company_name), slug(source), slug(relation), slug(target))
    )
