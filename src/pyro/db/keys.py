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
    """Lowercase, collapse non-alphanumerics to "-", trim.

    Deliberately lossy: "Auth Service" / "auth-service" / "auth/service" collapse to the same
    key, absorbing punctuation/casing drift before the merge pass needs a model call for it.
    Never returns "" (falls back to "x") and never emits a doubled "-", so "--" is safe as the
    field separator below.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "x"


def entity_key(company_name: str, name: str) -> str:
    return f"{slug(company_name)}--{slug(name)}"


def relationship_key(
    company_name: str, source: str, relation: str, target: str
) -> str:
    """Keyed on the canonical predicate so re-merging overwrites rather than duplicates, and
    differently-worded mentions of the same connection converge on one edge."""
    return "--".join(
        (slug(company_name), slug(source), slug(relation), slug(target))
    )
