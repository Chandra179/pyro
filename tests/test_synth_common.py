from pyro.synth.common import slugify, strip_outer_markdown_fence


def test_strips_outer_markdown_fence():
    wrapped = "```markdown\n# Title\n\nbody text\n```"
    assert strip_outer_markdown_fence(wrapped) == "# Title\n\nbody text"


def test_strips_outer_fence_without_language_tag():
    wrapped = "```\n# Title\n\nbody\n```"
    assert strip_outer_markdown_fence(wrapped) == "# Title\n\nbody"


def test_leaves_unwrapped_document_unchanged():
    text = "# Title\n\nbody with an inline ```code``` span"
    assert strip_outer_markdown_fence(text) == text


def test_leaves_document_with_internal_fences_unchanged():
    # only an outer fence wrapping the *entire* document should be stripped —
    # a real ```mermaid block inside the document must survive untouched.
    text = "# Title\n\n```mermaid\ngraph TD\n  A --> B\n```\n\nmore text"
    assert strip_outer_markdown_fence(text) == text


def test_slugify_normalizes_domain_names():
    assert slugify("Messaging & Real-Time") == "messaging-real-time"
    assert slugify("Other") == "other"
