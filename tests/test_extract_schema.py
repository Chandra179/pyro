import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pyro.config import Settings
from pyro.extract.pipeline import ExtractionRunConfig, extract_chunk
from pyro.extract.schema import (
    ExtractedEntity,
    ExtractedGraph,
    ExtractedRelationship,
    merge_graph_chunks,
)


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _settings_with_paid_key() -> Settings:
    """A configured paid-tier key so cascade_entrypoint resolves to a real group and
    _run_model_cascade actually calls into the (fake) router below."""
    return Settings(_env_file=None, groq_api_key="test-key")


def _fake_router(*, return_value=None, side_effect=None):
    """Duck-typed stand-in for litellm.Router: only `.acompletion` is ever called."""
    acompletion = AsyncMock(return_value=return_value, side_effect=side_effect)
    return SimpleNamespace(acompletion=acompletion), acompletion


def _config(router) -> ExtractionRunConfig:
    return ExtractionRunConfig(
        router=router,
        system_prompt="sys",
        user_template="user {title}",
        settings=_settings_with_paid_key(),
    )


VALID_JSON = json.dumps(
    {
        "entities": [
            {"name": "Zuul", "kind": "service", "domain": "Authentication"},
        ],
        "relationships": [],
    }
)


@pytest.mark.asyncio
async def test_first_model_success_no_fallback():
    router, mock_call = _fake_router(return_value=_fake_response(VALID_JSON))
    graph = await extract_chunk("t", "u", "c", _config(router))
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 1


@pytest.mark.asyncio
async def test_falls_back_on_malformed_json():
    responses = [_fake_response("not json at all"), _fake_response(VALID_JSON)]
    router, mock_call = _fake_router(side_effect=responses)
    graph = await extract_chunk("t", "u", "c", _config(router))
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_falls_back_on_schema_invalid_json():
    bad = json.dumps({"entities": "not a list"})  # wrong type
    responses = [_fake_response(bad), _fake_response(VALID_JSON)]
    router, mock_call = _fake_router(side_effect=responses)
    graph = await extract_chunk("t", "u", "c", _config(router))
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_repairs_markdown_fenced_json():
    fenced = f"```json\n{VALID_JSON}\n```"
    router, _ = _fake_router(return_value=_fake_response(fenced))
    graph = await extract_chunk("t", "u", "c", _config(router))
    assert graph.entities[0].name == "Zuul"


@pytest.mark.asyncio
async def test_all_models_fail_raises():
    router, mock_call = _fake_router(side_effect=RuntimeError("503 outage"))
    config = _config(router)
    with pytest.raises(RuntimeError, match="all models in cascade failed"):
        await extract_chunk("t", "u", "c", config)
    assert mock_call.call_count == config.settings.router.cascade_parse_retry_attempts


def test_merge_graph_chunks_dedupes_entities_case_insensitively():
    g1 = ExtractedGraph(entities=[ExtractedEntity(name="Cassandra", kind="datastore")])
    g2 = ExtractedGraph(entities=[ExtractedEntity(name="cassandra", kind="datastore")])
    merged = merge_graph_chunks([g1, g2])
    assert [e.name for e in merged.entities] == ["Cassandra"]


def test_merge_graph_chunks_dedupes_relationships_by_source_target_relation():
    rel = ExtractedRelationship(source="A", target="B", relation="calls")
    same_rel_different_case = ExtractedRelationship(source="a", target="b", relation="Calls")
    g1 = ExtractedGraph(relationships=[rel])
    g2 = ExtractedGraph(relationships=[same_rel_different_case])
    merged = merge_graph_chunks([g1, g2])
    assert len(merged.relationships) == 1


def test_merge_graph_chunks_empty_list():
    merged = merge_graph_chunks([])
    assert merged.entities == []
    assert merged.relationships == []
