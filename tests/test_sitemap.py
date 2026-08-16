from pyro.scrape.sitemap import _is_article_url


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
