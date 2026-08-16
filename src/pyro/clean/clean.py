"""Boilerplate stripping + code-block collapsing (plan.md 'Cleaning & Chunking Phase')."""

from __future__ import annotations

import re

import trafilatura
from bs4 import BeautifulSoup, Tag

from pyro.config import CleanConfig

_DEFAULT_CLEAN_CONFIG = CleanConfig()


def clean_html(
    raw_html: str,
    code_block_line_threshold: int = 15,
    clean_config: CleanConfig = _DEFAULT_CLEAN_CONFIG,
) -> str:
    """Strip nav/boilerplate, collapse large code blocks, return normalized article text.

    Main-content extraction is delegated to trafilatura (handles arbitrary blog
    markup far more robustly than a fixed tag/selector blocklist). Falls back to
    the raw document when trafilatura can't confidently isolate an article body
    (e.g. very short fixture/test pages).
    """
    extracted = trafilatura.extract(
        raw_html,
        output_format="html",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    soup = BeautifulSoup(extracted or raw_html, "lxml")

    for tag_name in clean_config.boilerplate_tags:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for selector in clean_config.boilerplate_selectors:
        for tag in soup.select(selector):
            tag.decompose()

    root = soup.find("article") or soup.find("main") or soup.body or soup

    _collapse_code_blocks(root, code_block_line_threshold)

    text = root.get_text(separator="\n", strip=True)
    return _normalize_whitespace(text)


def _collapse_code_blocks(root: Tag, line_threshold: int) -> None:
    # Only the outermost <pre> in a nesting chain — trafilatura's HTML output can
    # nest <pre><pre>...</pre></pre> for a single code block.
    for pre in root.find_all("pre"):
        if pre.find_parent("pre") is not None:
            continue
        code_text = pre.get_text()
        line_count = code_text.count("\n") + 1
        if line_count <= line_threshold:
            continue
        lang = None
        code_tag = pre.find("code")
        if code_tag is not None:
            for cls in code_tag.get("class", []):
                if cls.startswith("language-"):
                    lang = cls.removeprefix("language-")
                    break
        placeholder = (
            f"[code omitted: {line_count} lines"
            + (f", language: {lang}" if lang else "")
            + "]"
        )
        pre.replace_with(placeholder)


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
