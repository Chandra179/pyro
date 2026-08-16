import gzip

import httpx
import pytest

from pyro.scrape.sitemap import _is_article_url, fetch_sitemap_urls


def test_tagged_pages_filtered():
    assert not _is_article_url("https://netflixtechblog.com/tagged/netflix-api")


def test_category_and_author_pages_filtered():
    assert not _is_article_url("https://eng.uber.com/category/engineering/")
    assert not _is_article_url("https://eng.uber.com/author/jane-doe/")


def test_real_article_url_kept():
    assert _is_article_url(
        "https://netflixtechblog.com/machine-learning-for-fraud-detection-in-streaming-services-b0b4ef3be3f6"
    )


def test_base_url_filtered():
    assert not _is_article_url("https://netflixtechblog.com")
    assert not _is_article_url("https://netflixtechblog.com/")


def test_about_page_filtered():
    assert not _is_article_url("https://netflixtechblog.com/about")


@pytest.mark.asyncio
async def test_gzip_compressed_sitemap_is_decompressed():
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://example.com/some-real-post</loc></url>"
        b"</urlset>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(xml),
            headers={"content-type": "application/gzip"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    urls = await fetch_sitemap_urls("https://example.com/sitemap.xml.gz", client=client)
    assert urls == ["https://example.com/some-real-post"]
