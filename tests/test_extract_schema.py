import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pyro.extract.pipeline import extract_chunk
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
    with patch(
        "pyro.extract.pipeline.acompletion",
        new=AsyncMock(return_value=_fake_response(VALID_JSON)),
    ) as mock_call:
        graph = await extract_chunk(
            "t",
            "u",
            "c",
            [{"model": "model-a"}, {"model": "model-b"}],
            "sys",
            "user {title}",
        )
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 1


@pytest.mark.asyncio
async def test_falls_back_on_malformed_json():
    responses = [_fake_response("not json at all"), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        graph = await extract_chunk(
            "t",
            "u",
            "c",
            [{"model": "model-a"}, {"model": "model-b"}],
            "sys",
            "user {title}",
        )
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_falls_back_on_schema_invalid_json():
    bad = json.dumps({"entities": "not a list"})  # wrong type
    responses = [_fake_response(bad), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        graph = await extract_chunk(
            "t",
            "u",
            "c",
            [{"model": "model-a"}, {"model": "model-b"}],
            "sys",
            "user {title}",
        )
    assert graph.entities[0].name == "Zuul"
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_repairs_markdown_fenced_json():
    fenced = f"```json\n{VALID_JSON}\n```"
    with patch(
        "pyro.extract.pipeline.acompletion",
        new=AsyncMock(return_value=_fake_response(fenced)),
    ):
        graph = await extract_chunk(
            "t", "u", "c", [{"model": "model-a"}], "sys", "user {title}"
        )
    assert graph.entities[0].name == "Zuul"


@pytest.mark.asyncio
async def test_all_models_fail_raises():
    mock_call = AsyncMock(side_effect=RuntimeError("503 outage"))
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        with pytest.raises(RuntimeError, match="all models in cascade failed"):
            await extract_chunk(
                "t",
                "u",
                "c",
                [{"model": "model-a"}, {"model": "model-b"}],
                "sys",
                "user {title}",
            )
    assert mock_call.call_count == 2


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
