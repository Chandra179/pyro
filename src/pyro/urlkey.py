"""URL normalization for SQLite dedup keys (plan.md section 2)."""

import re
from urllib.parse import urlsplit

_MEDIUM_HEX_ID_RE = re.compile(r"-([0-9a-f]{12})/?$")


def normalize_url(url: str) -> str:
    """Return a stable dedup key for a URL.

    Medium-hosted posts end their path in a 12-char hex ID
    (e.g. ".../modeling-device-capabilities-for-analytics-e7607acebde8" -> "e7607acebde8").
    Everything else falls back to a normalized path slug: query/fragment stripped,
    trailing slash removed, leading/trailing slashes trimmed.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/")

    match = _MEDIUM_HEX_ID_RE.search(path)
    if match:
        return match.group(1)

    return path.lstrip("/") or parts.netloc
