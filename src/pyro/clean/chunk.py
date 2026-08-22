"""Token-threshold chunking with overlap for outlier posts (docs/architecture.md, "The layers" — Cleaning)."""

from __future__ import annotations

import tiktoken

_ENCODING_NAME = "cl100k_base"
_encoding = tiktoken.get_encoding(_ENCODING_NAME)


def estimate_tokens(text: str) -> int:
    return len(_encoding.encode(text))


def chunk_text(
    text: str,
    token_threshold: int = 8000,
    overlap_tokens: int = 500,
) -> list[str]:
    """Split text into overlapping chunks if it exceeds token_threshold, else return [text]."""
    tokens = _encoding.encode(text)
    if len(tokens) <= token_threshold:
        return [text]

    if overlap_tokens >= token_threshold:
        raise ValueError("overlap_tokens must be smaller than token_threshold")

    step = token_threshold - overlap_tokens
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + token_threshold, len(tokens))
        chunks.append(_encoding.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += step
    return chunks
