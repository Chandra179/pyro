"""LiteLLM Router cascade config (docs/architecture.md, "The layers" — Model routing).

Tiers are included only when their env-var key is configured, so the cascade degrades gracefully.
Order, cheapest/free first: OpenRouter curated free models (verified to support `response_format`)
-> `openrouter/free` meta-router as a self-updating safety net against catalog rotation -> Groq
free tier -> direct Gemini -> OpenCode Zen free aliases -> TokenRouter free aliases -> TokenRouter
paid (generic `openai/`+`api_base` passthrough) -> OpenAI gpt-4o-mini as last resort.
"""

from __future__ import annotations

import litellm
from litellm import Router

from pyro.config import Settings

_NO_PROVIDER_ERROR = "No LLM provider configured — set at least OPENROUTER_API_KEY."


def _max_tokens_for(model: str, fallback: int) -> int:
    """Each tier's real output cap from litellm's registry; falls back to `fallback` for custom
    passthrough aliases litellm doesn't recognize (TokenRouter/OpenCode Zen model IDs)."""
    try:
        return litellm.get_max_tokens(model) or fallback
    except Exception:
        return fallback


def build_model_list(settings: Settings) -> list[dict]:
    model_list: list[dict] = []

    default_max_tokens = settings.extraction_max_tokens

    if settings.openrouter_api_key:
        for model_name in settings.router.openrouter_free_models:
            model_list.append(
                {
                    "model_name": "extraction-cascade",
                    "litellm_params": {
                        "model": model_name,
                        "api_key": settings.openrouter_api_key,
                        "max_tokens": _max_tokens_for(model_name, default_max_tokens),
                    },
                }
            )

    if settings.groq_api_key:
        model_list.append(
            {
                "model_name": "extraction-cascade",
                "litellm_params": {
                    "model": settings.router.groq_model,
                    "api_key": settings.groq_api_key,
                    "max_tokens": _max_tokens_for(
                        settings.router.groq_model, default_max_tokens
                    ),
                },
            }
        )

    gemini_key = settings.google_api_key or settings.gemini_api_key
    if gemini_key:
        model_list.append(
            {
                "model_name": "extraction-cascade",
                "litellm_params": {
                    "model": settings.router.gemini_model,
                    "api_key": gemini_key,
                    "max_tokens": _max_tokens_for(
                        settings.router.gemini_model, default_max_tokens
                    ),
                },
            }
        )

    if settings.opencode_api_key:
        for model_name in settings.router.opencode_free_models:
            model_list.append(
                {
                    "model_name": "extraction-cascade",
                    "litellm_params": {
                        "model": f"openai/{model_name}",
                        "api_base": settings.router.opencode_api_base,
                        "api_key": settings.opencode_api_key,
                        "max_tokens": default_max_tokens,  # not in litellm's registry
                    },
                }
            )

    if settings.tokenrouter_api_key:
        model_list.append(
            {
                "model_name": "extraction-cascade",
                "litellm_params": {
                    "model": f"openai/{settings.router.tokenrouter_model}",
                    "api_base": settings.router.tokenrouter_api_base,
                    "api_key": settings.tokenrouter_api_key,
                    "max_tokens": default_max_tokens,  # tokenrouter alias, not in litellm's registry
                },
            }
        )

    if settings.openai_api_key:
        model_list.append(
            {
                "model_name": "extraction-cascade",
                "litellm_params": {
                    "model": settings.router.openai_model,
                    "api_key": settings.openai_api_key,
                    "max_tokens": _max_tokens_for(
                        settings.router.openai_model, default_max_tokens
                    ),
                },
            }
        )

    return model_list


def build_router(settings: Settings | None = None) -> Router:
    settings = settings or Settings()
    model_list = build_model_list(settings)
    if not model_list:
        raise RuntimeError(_NO_PROVIDER_ERROR)
    return Router(
        model_list=model_list,
        num_retries=settings.router.num_retries,
        cooldown_time=settings.router.cooldown_time,
        allowed_fails=settings.router.allowed_fails,
        timeout=settings.router.timeout,
        stream_timeout=settings.router.stream_timeout,
    )


def concrete_model_names(settings: Settings | None = None) -> list[str]:
    """The ordered list of concrete `litellm_params.model` values in the cascade.

    Used by the extraction pipeline's schema-validation-retry loop, which advances through
    concrete models directly since Router fallback only fires on raised exceptions.
    """
    settings = settings or Settings()
    return [entry["litellm_params"]["model"] for entry in build_model_list(settings)]


def graph_model_params(settings: Settings | None = None) -> dict:
    """Credentials for the single-shot high-reasoning graph-merge pass.

    Uses `settings.graph_model` when OPENROUTER_API_KEY is configured; otherwise falls back to
    the last (highest-capability) tier in the extraction cascade, so merging still runs when only
    a subset of providers is configured.
    """
    settings = settings or Settings()
    if settings.openrouter_api_key and settings.graph_model.startswith(
        "openrouter/"
    ):
        return {
            "model": settings.graph_model,
            "api_key": settings.openrouter_api_key,
        }

    model_list = build_model_list(settings)
    if not model_list:
        raise RuntimeError(_NO_PROVIDER_ERROR)
    return dict(model_list[-1]["litellm_params"])


def concrete_model_params(settings: Settings | None = None) -> list[dict]:
    """Like `concrete_model_names`, but also carries each tier's `api_key`/`api_base` so callers
    bypassing the `Router` object (extract/pipeline.py) can authenticate against tiers outside
    litellm's default env-var naming convention (e.g. TokenRouter's `openai/` passthrough).
    """
    settings = settings or Settings()
    return [dict(entry["litellm_params"]) for entry in build_model_list(settings)]
