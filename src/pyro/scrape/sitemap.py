"""Sitemap XML discovery — the master URL list (plan.md 'Raw Phase')."""

from __future__ import annotations

import httpx
from lxml import etree

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Some blogs (e.g. Medium-hosted ones) serve a JS app shell instead of raw XML
# to clients without a browser-like User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; pyro-blog-crawler/1.0)"}

# Blog platforms commonly list tag/category/author index pages alongside actual
# posts in their sitemap. These carry no article content, so they're filtered
# generically (not company-specific) rather than scraped and wasted on extraction.
_NON_ARTICLE_PATH_SEGMENTS = ("/tagged/", "/tag/", "/category/", "/categories/", "/author/")


def _is_article_url(url: str) -> bool:
    return not any(segment in url for segment in _NON_ARTICLE_PATH_SEGMENTS)


async def fetch_sitemap_urls(sitemap_url: str, client: httpx.AsyncClient | None = None) -> list[str]:
    """Fetch a sitemap.xml (or sitemap index) and return every <loc> URL that
    looks like an article (tag/category/author index pages filtered out).

    Recurses one level into nested sitemap indexes (<sitemapindex> of <sitemap> entries).
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS)
    try:
        urls = await _fetch_locs(sitemap_url, client)
        return [u for u in urls if _is_article_url(u)]
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_locs(url: str, client: httpx.AsyncClient) -> list[str]:
    resp = await client.get(url)
    resp.raise_for_status()
    try:
        root = etree.fromstring(resp.content)
    except etree.XMLSyntaxError as exc:
        raise ValueError(
            f"{url} did not return valid XML (got content-type "
            f"{resp.headers.get('content-type')!r}) — the blog may be serving an "
            "HTML app shell instead of the sitemap; check the seed URL "
            "(Medium blogs often use /sitemap/sitemap.xml, not /sitemap.xml)"
        ) from exc

    tag = etree.QName(root.tag).localname
    if tag == "sitemapindex":
        child_sitemaps = [
            loc.text.strip()
            for loc in root.findall(".//sm:sitemap/sm:loc", namespaces=_NS)
            if loc.text
        ]
        urls: list[str] = []
        for child_url in child_sitemaps:
            urls.extend(await _fetch_locs(child_url, client))
        return urls

    return [loc.text.strip() for loc in root.findall(".//sm:url/sm:loc", namespaces=_NS) if loc.text]
