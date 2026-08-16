import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pyro.extract.pipeline import extract_chunk
from pyro.extract.schema import ExtractedFacts, merge_facts


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


VALID_JSON = json.dumps(
    {
        "domain": "Authentication",
        "topic": "Zuul API Gateway routes edge traffic.",
        "problem": "The monolith couldn't scale routing decisions.",
        "solution": "Introduced a dedicated edge gateway with dynamic routing.",
    }
)


@pytest.mark.asyncio
async def test_first_model_success_no_fallback():
    with patch("pyro.extract.pipeline.acompletion", new=AsyncMock(return_value=_fake_response(VALID_JSON))) as mock_call:
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}], "sys", "user {title}")
    assert facts.topic == "Zuul API Gateway routes edge traffic."
    assert mock_call.call_count == 1


@pytest.mark.asyncio
async def test_falls_back_on_malformed_json():
    responses = [_fake_response("not json at all"), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}], "sys", "user {title}")
    assert facts.topic == "Zuul API Gateway routes edge traffic."
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_falls_back_on_schema_invalid_json():
    bad = json.dumps({"topic": 12345})  # wrong type
    responses = [_fake_response(bad), _fake_response(VALID_JSON)]
    mock_call = AsyncMock(side_effect=responses)
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}], "sys", "user {title}")
    assert facts.topic == "Zuul API Gateway routes edge traffic."
    assert mock_call.call_count == 2


@pytest.mark.asyncio
async def test_repairs_markdown_fenced_json():
    fenced = f"```json\n{VALID_JSON}\n```"
    with patch("pyro.extract.pipeline.acompletion", new=AsyncMock(return_value=_fake_response(fenced))):
        facts = await extract_chunk("t", "u", "c", [{"model": "model-a"}], "sys", "user {title}")
    assert facts.topic == "Zuul API Gateway routes edge traffic."


@pytest.mark.asyncio
async def test_all_models_fail_raises():
    mock_call = AsyncMock(side_effect=RuntimeError("503 outage"))
    with patch("pyro.extract.pipeline.acompletion", new=mock_call):
        with pytest.raises(RuntimeError, match="all models in cascade failed"):
            await extract_chunk("t", "u", "c", [{"model": "model-a"}, {"model": "model-b"}], "sys", "user {title}")
    assert mock_call.call_count == 2


def test_merge_facts_joins_unique_parts_across_chunks():
    f1 = ExtractedFacts(domain="Authentication", topic="Zuul routes traffic.", problem="Scaling.", solution="")
    f2 = ExtractedFacts(domain="Authentication", topic="Zuul routes traffic.", problem="", solution="Dynamic routing.")
    merged = merge_facts([f1, f2])
    assert merged.domain == "Authentication"
    assert merged.topic == "Zuul routes traffic."
    assert merged.problem == "Scaling."
    assert merged.solution == "Dynamic routing."


def test_merge_facts_falls_back_to_other_for_invalid_domain():
    f1 = ExtractedFacts(domain="Bogus", topic="t")
    merged = merge_facts([f1])
    assert merged.domain == "Other"


def test_merge_facts_empty_list():
    merged = merge_facts([])
    assert merged.topic == ""
