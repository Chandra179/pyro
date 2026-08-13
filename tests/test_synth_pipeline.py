from pyro.db import Article
from pyro.synth.pipeline import _group_by_domain, _strip_outer_markdown_fence, slugify


def test_strips_outer_markdown_fence():
    wrapped = "```markdown\n# Title\n\nbody text\n```"
    assert _strip_outer_markdown_fence(wrapped) == "# Title\n\nbody text"


def test_strips_outer_fence_without_language_tag():
    wrapped = "```\n# Title\n\nbody\n```"
    assert _strip_outer_markdown_fence(wrapped) == "# Title\n\nbody"


def test_leaves_unwrapped_document_unchanged():
    text = "# Title\n\nbody with an inline ```code``` span"
    assert _strip_outer_markdown_fence(text) == text


def test_leaves_document_with_internal_fences_unchanged():
    # only an outer fence wrapping the *entire* document should be stripped —
    # a real ```mermaid block inside the document must survive untouched.
    text = "# Title\n\n```mermaid\ngraph TD\n  A --> B\n```\n\nmore text"
    assert _strip_outer_markdown_fence(text) == text


def test_slugify_normalizes_domain_names():
    assert slugify("Messaging & Real-Time") == "messaging-real-time"
    assert slugify("Other") == "other"


def _article(domain: str | None) -> Article:
    return Article(
        id=domain or "none",
        source_url="u",
        company_name="c",
        extracted_facts={"domain": domain} if domain else {},
    )


def test_group_by_domain_defaults_missing_domain_to_other():
    articles = [_article("Authentication"), _article("Authentication"), _article(None)]
    groups = _group_by_domain(articles)
    assert set(groups) == {"Authentication", "Other"}
    assert len(groups["Authentication"]) == 2
    assert len(groups["Other"]) == 1
