from pyro.urlkey import normalize_url


def test_medium_hex_id_extracted():
    url = "https://netflixtechblog.com/modeling-device-capabilities-for-analytics-e7607acebde8"
    assert normalize_url(url) == "e7607acebde8"


def test_medium_hex_id_with_trailing_slash():
    url = "https://netflixtechblog.com/modeling-device-capabilities-for-analytics-e7607acebde8/"
    assert normalize_url(url) == "e7607acebde8"


def test_non_medium_uses_path_slug():
    url = "https://eng.uber.com/blog/some-cool-architecture-post/?utm_source=twitter"
    assert normalize_url(url) == "blog/some-cool-architecture-post"


def test_non_medium_strips_query_and_fragment():
    url = "https://stripe.com/blog/engineering/scaling-payments#section-2"
    assert normalize_url(url) == "blog/engineering/scaling-payments"


def test_same_article_different_query_dedupes_to_same_key():
    a = "https://eng.uber.com/blog/post/?ref=home"
    b = "https://eng.uber.com/blog/post/?ref=newsletter"
    assert normalize_url(a) == normalize_url(b)
