import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pyro.extract.pipeline import extract_chunk
from pyro.extract.schema import Entity, ExtractedFacts, merge_facts


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


VALID_JSON = json.dumps(
    {
        "is_architectural": True,
        "primary_entities": [
            {
                "canonical_name": "Zuul API Gateway",
                "domain_tags": ["Edge", "Routing"],
                "description": "Edge gateway.",
                "tech_stack": ["Java"],
                "patterns_and_concepts": ["Circuit Breaker"],
            }
        ],
        "system_integrations": [],
        "evolution_notes": [],
    }
)


@pytest.mark.asyncio
async def test_first_model_success_no_fallback():
    with patch("pyro.extract.pipeline.acompletion", new=AsyncMock(return_value=_fake_response(VALID_JSON))) as mock_call:
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}])
    assert facts.is_architectural is True
    assert facts.primary_entities[0].canonical_name == "Zuul API Gateway"
    assert mock_call.call_count == 1


@pytest.mark.asyncio
async def test_falls_back_on_malformed_json():
    responses = [_fake_response("not json at all"), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}])
    assert facts.is_architectural is True
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_falls_back_on_schema_invalid_json():
    bad = json.dumps({"primary_entities": "not-a-list"})  # missing is_architectural, wrong type
    responses = [_fake_response(bad), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}])
    assert facts.is_architectural is True
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_repairs_markdown_fenced_json():
    fenced = f"```json\n{VALID_JSON}\n```"
    with patch("pyro.extract.pipeline.acompletion", new=AsyncMock(return_value=_fake_response(fenced))):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}])
    assert facts.is_architectural is True


@pytest.mark.asyncio
async def test_all_models_fail_raises():
    mock_call = AsyncMock(side_effect=RuntimeError("503 outage"))
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        with pytest.raises(RuntimeError, match="all models in cascade failed"):
            await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}])
    assert mock_call.call_count == 2


def test_merge_facts_dedupes_entities_by_canonical_name():
    f1 = ExtractedFacts(
        is_architectural=True,
        primary_entities=[
            Entity(canonical_name="Zuul", domain_tags=["Edge"], tech_stack=["Java"])
        ],
    )
    f2 = ExtractedFacts(
        is_architectural=True,
        primary_entities=[
            Entity(canonical_name="zuul", domain_tags=["Routing"], tech_stack=["Netty"])
        ],
    )
    merged = merge_facts([f1, f2])
    assert len(merged.primary_entities) == 1
    entity = merged.primary_entities[0]
    assert set(entity.domain_tags) == {"Edge", "Routing"}
    assert set(entity.tech_stack) == {"Java", "Netty"}


def test_merge_facts_empty_list():
    merged = merge_facts([])
    assert merged.is_architectural is False
    assert merged.primary_entities == []
