"""SQLite storage for the articles pipeline state (plan.md 'SQLite Schema')."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT,
    company_name TEXT NOT NULL,
    raw_html TEXT,
    cleaned_text TEXT,
    is_architectural INTEGER,
    extracted_facts TEXT,
    scraped_at TEXT,
    extracted_at TEXT
);
"""


@dataclass
class Article:
    id: str
    source_url: str
    company_name: str
    title: str | None = None
    raw_html: str | None = None
    cleaned_text: str | None = None
    is_architectural: bool | None = None
    extracted_facts: dict[str, Any] | None = None
    scraped_at: str | None = None
    extracted_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Article:
        facts = json.loads(row["extracted_facts"]) if row["extracted_facts"] else None
        is_arch = row["is_architectural"]
        return cls(
            id=row["id"],
            source_url=row["source_url"],
            title=row["title"],
            company_name=row["company_name"],
            raw_html=row["raw_html"],
            cleaned_text=row["cleaned_text"],
            is_architectural=None if is_arch is None else bool(is_arch),
            extracted_facts=facts,
            scraped_at=row["scraped_at"],
            extracted_at=row["extracted_at"],
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert_raw(
        self, id: str, source_url: str, title: str | None, company_name: str, raw_html: str
    ) -> None:
        """Insert a newly scraped article. No-ops if the id already exists (dedup)."""
        self._conn.execute(
            """
            INSERT INTO articles (id, source_url, title, company_name, raw_html, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (id, source_url, title, company_name, raw_html, _now()),
        )
        self._conn.commit()

    def exists(self, id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM articles WHERE id = ?", (id,)).fetchone()
        return row is not None

    def mark_cleaned(self, id: str, cleaned_text: str) -> None:
        self._conn.execute(
            "UPDATE articles SET cleaned_text = ? WHERE id = ?", (cleaned_text, id)
        )
        self._conn.commit()

    def mark_extracted(
        self, id: str, is_architectural: bool, extracted_facts: dict[str, Any]
    ) -> None:
        self._conn.execute(
            """
            UPDATE articles
            SET is_architectural = ?, extracted_facts = ?, extracted_at = ?
            WHERE id = ?
            """,
            (int(is_architectural), json.dumps(extracted_facts), _now(), id),
        )
        self._conn.commit()

    def fetch_unprocessed(self, stage: str, limit: int | None = None) -> list[Article]:
        """stage: 'clean' (raw_html set, cleaned_text null) or
        'extract' (cleaned_text set, extracted_at null)."""
        if stage == "clean":
            query = "SELECT * FROM articles WHERE raw_html IS NOT NULL AND cleaned_text IS NULL"
        elif stage == "extract":
            query = "SELECT * FROM articles WHERE cleaned_text IS NOT NULL AND extracted_at IS NULL"
        else:
            raise ValueError(f"unknown stage: {stage}")
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = self._conn.execute(query).fetchall()
        return [Article.from_row(r) for r in rows]

    def fetch_architectural(self, company_name: str) -> list[Article]:
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE company_name = ? AND is_architectural = 1",
            (company_name,),
        ).fetchall()
        return [Article.from_row(r) for r in rows]


@contextmanager
def open_db(path: str | Path) -> Iterator[Database]:
    db = Database(path)
    try:
        yield db
    finally:
        db.close()
