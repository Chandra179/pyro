from pathlib import Path

from pyro.clean.clean import clean_html

FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.html"


def test_strips_boilerplate():
    html = FIXTURE.read_text()
    cleaned = clean_html(html)
    assert "navigation links" not in cleaned
    assert "Header banner" not in cleaned
    assert "Footer copyright" not in cleaned
    assert "Related posts" not in cleaned
    assert "Great post!" not in cleaned


def test_keeps_article_content():
    html = FIXTURE.read_text()
    cleaned = clean_html(html)
    assert "Edge Gateway" in cleaned
    assert "Zuul-based edge gateway" in cleaned


def test_collapses_large_code_block():
    html = FIXTURE.read_text()
    cleaned = clean_html(html, code_block_line_threshold=15)
    assert "line1" not in cleaned
    assert "[code omitted:" in cleaned


def test_small_code_block_not_collapsed():
    html = """
    <html><body><article>
    <p>intro</p>
    <pre><code>a\nb\nc</code></pre>
    </article></body></html>
    """
    cleaned = clean_html(html, code_block_line_threshold=15)
    assert "[code omitted" not in cleaned
    assert "a" in cleaned
