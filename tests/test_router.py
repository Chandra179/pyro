import pytest

from pyro.config import Settings
from pyro.router import (
    build_model_list,
    build_router,
    concrete_model_names,
    concrete_model_params,
)


def test_no_keys_yields_empty_model_list():
    settings = Settings(_env_file=None)
    assert build_model_list(settings) == []


def test_openrouter_only_includes_curated_free_models_plus_meta_router():
    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    names = concrete_model_names(settings)
    assert names == [
        "openrouter/openai/gpt-oss-20b:free",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/google/gemma-4-31b-it:free",
        "openrouter/openrouter/free",
    ]


def test_groq_gemini_tokenrouter_and_openai_appended_when_configured():
    settings = Settings(
        _env_file=None,
        openrouter_api_key="or-key",
        groq_api_key="gq-key",
        google_api_key="g-key",
        tokenrouter_api_key="tr-key",
        openai_api_key="oa-key",
    )
    names = concrete_model_names(settings)
    assert names[-4] == "groq/llama-3.3-70b-versatile"
    assert names[-3] == "gemini/gemini-2.5-flash"
    assert names[-2] == "openai/deepseek/deepseek-v4-flash-0731"
    assert names[-1] == "gpt-4o-mini"
    assert len(names) == 8


def test_tokenrouter_only_tier_uses_openai_compatible_passthrough():
    settings = Settings(_env_file=None, tokenrouter_api_key="tr-key")
    model_list = build_model_list(settings)
    assert len(model_list) == 1
    params = model_list[0]["litellm_params"]
    assert params["model"] == "openai/deepseek/deepseek-v4-flash-0731"
    assert params["api_base"] == "https://api.tokenrouter.com/v1"
    assert params["api_key"] == "tr-key"


def test_concrete_model_params_carries_credentials_for_direct_acompletion_calls():
    settings = Settings(_env_file=None, tokenrouter_api_key="tr-key")
    params = concrete_model_params(settings)
    assert params == [
        {
            "model": "openai/deepseek/deepseek-v4-flash-0731",
            "api_base": "https://api.tokenrouter.com/v1",
            "api_key": "tr-key",
            "max_tokens": settings.extraction_max_tokens,
        }
    ]


def test_groq_only_tier_included_when_configured_alone():
    settings = Settings(_env_file=None, groq_api_key="gq-key")
    assert concrete_model_names(settings) == ["groq/llama-3.3-70b-versatile"]


def test_build_router_raises_without_any_key():
    settings = Settings(_env_file=None)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_router(settings)


def test_build_router_succeeds_with_openrouter_key():
    settings = Settings(_env_file=None, openrouter_api_key="or-key")
    router = build_router(settings)
    assert router is not None
