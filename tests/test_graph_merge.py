"""Coverage for graph/merge.py's run_graph_merge: per-article name resolution against the
company's existing entity names, and idempotency (already-merged articles are left alone)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pyro.config import Settings
from pyro.db import Article
from pyro.graph.merge import run_graph_merge


def _fake_stream_response(content: str):
    """Shaped like an acompletion(..., stream=True) async iterator: one chunk carrying the
    full content, matching what stream_with_rate_limit_retry expects to iterate."""

    class _Stream:
        def __aiter__(self):
            async def _gen():
                delta = SimpleNamespace(content=content, reasoning_content="")
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

            return _gen()

    return _Stream()


class _FakeDb:
    """Enough of Database's graph surface for run_graph_merge, without ArangoDB."""

    def __init__(self, extracted_articles, pending_articles):
        self._extracted = extracted_articles
        self._pending = list(pending_articles)
        self.entities: dict[str, dict] = {}
        self.relationships: list[dict] = []
        self.merged_ids: list[str] = []

    def fetch_extracted(self, company_name):
        return self._extracted

    def fetch_pending_merge(self, company_name):
        return self._pending

    def list_entity_names(self, company_name):
        return sorted(e["name"] for e in self.entities.values())

    def upsert_entity(self, company_name, name, kind, domain, alias=None, first_seen_article_id=None):
        self.entities[name] = {"name": name, "kind": kind, "domain": domain}
        return name

    def upsert_relationship(self, company_name, source, target, relation, as_of, source_article_id):
        self.relationships.append({"source": source, "target": target, "relation": relation})

    def list_entities(self, company_name):
        return list(self.entities.values())

    def list_relationships(self, company_name):
        return list(self.relationships)

    def mark_graph_merged(self, article_id):
        self.merged_ids.append(article_id)


def _article(id_, entities, relationships=()):
    return Article(
        id=id_,
        source_url=f"https://example.com/{id_}",
        company_name="acme",
        title=f"title-{id_}",
        extracted_at="2026-01-01T00:00:00+00:00",
        extracted_graph={"entities": entities, "relationships": list(relationships)},
    )


@pytest.mark.asyncio
async def test_run_graph_merge_resolves_reused_name_via_llm():
    """A second article's "serving system" should resolve to the first article's already-known
    "vLLM" when the merge LLM says so — the whole point of the merge pass."""
    a1 = _article("a1", entities=[{"name": "vLLM", "kind": "service", "domain": "Other"}])
    a2 = _article("a2", entities=[{"name": "serving system", "kind": "service", "domain": "Other"}])
    db = _FakeDb(extracted_articles=[a1, a2], pending_articles=[a1, a2])

    resolved_response = json.dumps(
        {"resolved": [{"article_name": "vLLM", "canonical_name": "vLLM"}]}
    )
    reuse_response = json.dumps(
        {"resolved": [{"article_name": "serving system", "canonical_name": "vLLM"}]}
    )
    responses = iter([resolved_response, reuse_response])

    async def fake_acompletion(**kwargs):
        return _fake_stream_response(next(responses))

    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    with patch("pyro.graph.merge.acompletion", new=AsyncMock(side_effect=fake_acompletion)):
        result = await run_graph_merge(db, settings, "acme")

    assert result["articles_merged"] == 2
    assert list(db.entities.keys()) == ["vLLM"]
    assert db.merged_ids == ["a1", "a2"]


@pytest.mark.asyncio
async def test_run_graph_merge_no_pending_articles_is_a_noop():
    a1 = _article("a1", entities=[{"name": "S3", "kind": "datastore", "domain": "Other"}])
    db = _FakeDb(extracted_articles=[a1], pending_articles=[])
    settings = Settings(_env_file=None, openrouter_api_key="or-key")

    with patch("pyro.graph.merge.acompletion", new=AsyncMock()) as mock_call:
        result = await run_graph_merge(db, settings, "acme")

    assert result["articles_merged"] == 0
    mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_run_graph_merge_raises_when_company_has_no_extracted_articles():
    db = _FakeDb(extracted_articles=[], pending_articles=[])
    settings = Settings(_env_file=None, openrouter_api_key="or-key")

    with pytest.raises(RuntimeError, match="no extracted articles"):
        await run_graph_merge(db, settings, "acme")
