import pytest

from pyro.clean.chunk import chunk_text, estimate_tokens


def test_short_text_not_chunked():
    text = "short article text"
    assert chunk_text(text, token_threshold=8000) == [text]


def test_long_text_is_chunked_with_overlap():
    text = "word " * 20_000
    chunks = chunk_text(text, token_threshold=8000, overlap_tokens=500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert estimate_tokens(chunk) <= 8000

    # overlap: the tail tokens of chunk[0] reappear at the head of chunk[1]
    overlap_text = "word " * 500
    assert chunks[1].strip().startswith(overlap_text.strip())


def test_chunks_cover_full_text_contiguously():
    text = "word " * 50_000
    chunks = chunk_text(text, token_threshold=8000, overlap_tokens=500)
    assert chunks[-1].rstrip().endswith("word")
    assert sum(estimate_tokens(c) for c in chunks) >= estimate_tokens(text)


def test_overlap_must_be_smaller_than_threshold():
    with pytest.raises(ValueError):
        chunk_text("word " * 30_000, token_threshold=100, overlap_tokens=100)
