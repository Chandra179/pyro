"""Typed views over stored documents. Only `Article` needs one — entities and relationships are
returned as plain dicts, since every consumer either renders them straight to a template or walks
them generically."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Article:
    id: str
    source_url: str
    company_name: str
    title: str | None = None
    raw_html: str | None = None
    cleaned_text: str | None = None
    extracted_graph: dict[str, Any] | None = None
    scraped_at: str | None = None
    extracted_at: str | None = None
    # Set once this article's extracted entities/relationships have been folded into the
    # company's graph (see graph.merge.run_graph_merge) — lets a merge run process only new
    # articles instead of replaying every article through the merge prompt on every run.
    graph_merged_at: str | None = None

    @classmethod
    def from_doc(cls, doc: dict) -> Article:
        return cls(
            id=doc["_key"],
            source_url=doc["source_url"],
            title=doc.get("title"),
            company_name=doc["company_name"],
            raw_html=doc.get("raw_html"),
            cleaned_text=doc.get("cleaned_text"),
            extracted_graph=doc.get("extracted_graph"),
            scraped_at=doc.get("scraped_at"),
            extracted_at=doc.get("extracted_at"),
            graph_merged_at=doc.get("graph_merged_at"),
        )
