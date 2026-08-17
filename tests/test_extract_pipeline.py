"""Coverage for extract/pipeline.py's run_extraction: bounded-concurrency orchestration over
many articles, where one article's cascade exhausting every model must not take down the rest
of the batch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pyro.config import Settings
from pyro.db import Article
from pyro.extract.pipeline import run_extraction


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


VALID_JSON = json.dumps(
    {
        "entities": [{"name": "Zuul", "kind": "service", "domain": "Authentication"}],
        "relationships": [],
    }
)


class _FakeDb:
    def __init__(self, articles):
        self._articles = articles
        self.marked: list[str] = []

    def fetch_unprocessed(self, stage, limit=None, company_name=None):
        assert stage == "extract"
        return self._articles

    def mark_extracted(self, article_id, graph):
        self.marked.append(article_id)


def _article(id_, cleaned_text="article body text"):
    return Article(
        id=id_,
        source_url=f"https://example.com/{id_}",
        company_name="acme",
        title=f"title-{id_}",
        cleaned_text=cleaned_text,
    )


@pytest.mark.asyncio
async def test_run_extraction_marks_every_successful_article():
    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    db = _FakeDb([_article("a1"), _article("a2"), _article("a3")])
    with patch(
        "pyro.extract.pipeline.acompletion",
        new=AsyncMock(return_value=_fake_response(VALID_JSON)),
    ):
        count = await run_extraction(db, settings)
    assert count == 3
    assert set(db.marked) == {"a1", "a2", "a3"}


@pytest.mark.asyncio
async def test_run_extraction_one_failed_cascade_does_not_block_the_rest():
    """"bad"'s cascade exhausts every model in the tier list; run_extraction's per-article
    try/except must catch that RuntimeError, log it, and leave "bad" unmarked — not propagate
    and abort "good", which is processed concurrently in the same asyncio.gather batch."""
    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    db = _FakeDb([_article("bad"), _article("good")])

    async def fake_acompletion(**kwargs):
        user_content = kwargs["messages"][1]["content"]
        if "/bad" in user_content:
            raise RuntimeError("503 from provider")
        return _fake_response(VALID_JSON)

    with patch(
        "pyro.extract.pipeline.acompletion", new=AsyncMock(side_effect=fake_acompletion)
    ):
        count = await run_extraction(db, settings)
    assert count == 2  # both articles were attempted
    assert db.marked == ["good"]  # only the one whose cascade actually succeeded


@pytest.mark.asyncio
async def test_run_extraction_no_unprocessed_articles_is_a_noop():
    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    db = _FakeDb([])
    with patch(
        "pyro.extract.pipeline.acompletion", new=AsyncMock()
    ) as mock_call:
        count = await run_extraction(db, settings)
    assert count == 0
    assert db.marked == []
    mock_call.assert_not_called()
