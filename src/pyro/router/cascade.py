"""LiteLLM Router cascade config (docs/architecture.md, "The layers" — Model routing).

Deployments group into two litellm Router `model_name` swimlanes, included only when their key is
configured: "extraction-paid" (Groq, Gemini, TokenRouter, OpenAI) and "extraction-free"
(OpenRouter's `:free` models + meta-router, OpenCode Zen). build_router wires
fallbacks=[{paid: [free]}], so free is only tried once paid is exhausted; within a group,
routing_strategy="simple-shuffle" picks randomly and allowed_fails/cooldown_time eject a
repeatedly-failing deployment.

OpenCode Go is deliberately absent here — litellm misclassified its responses as RateLimitError.
It's a separate opt-in tier called directly over httpx (router/opencode_go.py) after this Router
is exhausted — see extract/pipeline.py's `_run_model_cascade`, gated by `opencode_go_enabled`.
"""

from __future__ import annotations

import litellm
from litellm import Router

from pyro.config import Settings

_NO_PROVIDER_ERROR = "No LLM provider configured — set at least OPENROUTER_API_KEY."

PAID_GROUP = "extraction-paid"
FREE_GROUP = "extraction-free"


def _max_tokens_for(model: str, fallback: int) -> int:
    """Each tier's real output cap from litellm's registry; falls back to `fallback` for custom
    passthrough aliases litellm doesn't recognize (TokenRouter/OpenCode Zen model IDs)."""
    try:
        return litellm.get_max_tokens(model) or fallback
    except Exception:
        return fallback


def _paid_model_list(settings: Settings) -> list[dict]:
    model_list: list[dict] = []
    default_max_tokens = settings.extraction_max_tokens

    if settings.groq_api_key:
        model_list.append(
            {
                "model_name": PAID_GROUP,
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
                "model_name": PAID_GROUP,
                "litellm_params": {
                    "model": settings.router.gemini_model,
                    "api_key": gemini_key,
                    "max_tokens": _max_tokens_for(
                        settings.router.gemini_model, default_max_tokens
                    ),
                },
            }
        )

    if settings.tokenrouter_api_key:
        model_list.append(
            {
                "model_name": PAID_GROUP,
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
                "model_name": PAID_GROUP,
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


def _free_model_list(settings: Settings) -> list[dict]:
    model_list: list[dict] = []
    default_max_tokens = settings.extraction_max_tokens

    if settings.openrouter_api_key:
        for model_name in settings.router.openrouter_free_models:
            model_list.append(
                {
                    "model_name": FREE_GROUP,
                    "litellm_params": {
                        "model": model_name,
                        "api_key": settings.openrouter_api_key,
                        "max_tokens": _max_tokens_for(model_name, default_max_tokens),
                    },
                }
            )

    if settings.opencode_api_key:
        for model_name in settings.router.opencode_free_models:
            model_list.append(
                {
                    "model_name": FREE_GROUP,
                    "litellm_params": {
                        "model": f"openai/{model_name}",
                        "api_base": settings.router.opencode_api_base,
                        "api_key": settings.opencode_api_key,
                        "max_tokens": default_max_tokens,  # not in litellm's registry
                    },
                }
            )

    return model_list


def build_model_list(settings: Settings) -> list[dict]:
    """Paid-tier deployments first, then free-tier — same order build_router's fallback chain
    tries them in, and the order concrete_model_names/concrete_model_params expose."""
    return _paid_model_list(settings) + _free_model_list(settings)


def cascade_entrypoint(settings: Settings) -> str | None:
    """Which model_name group `_run_model_cascade` should call first: the paid group if any paid
    deployment is configured, else the free group, else None if neither has anything configured
    (OpenCode Go direct-call may still be usable as a last resort outside this Router)."""
    if _paid_model_list(settings):
        return PAID_GROUP
    if _free_model_list(settings):
        return FREE_GROUP
    return None


def build_router(settings: Settings | None = None) -> Router:
    settings = settings or Settings()
    paid = _paid_model_list(settings)
    free = _free_model_list(settings)
    model_list = paid + free
    if not model_list:
        raise RuntimeError(_NO_PROVIDER_ERROR)
    return Router(
        model_list=model_list,
        fallbacks=[{PAID_GROUP: [FREE_GROUP]}] if paid and free else None,
        routing_strategy=settings.router.routing_strategy,
        num_retries=settings.router.num_retries,
        cooldown_time=settings.router.cooldown_time,
        allowed_fails=settings.router.allowed_fails,
        timeout=settings.router.timeout,
        stream_timeout=settings.router.stream_timeout,
    )


def concrete_model_names(settings: Settings | None = None) -> list[str]:
    """The ordered list of concrete `litellm_params.model` values in the cascade (paid tier
    first, then free). Used by tests and by graph_model_params' last-resort fallback."""
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
    """Like `concrete_model_names`, but also carries each tier's `api_key`/`api_base`. Used by
    tests and by graph_model_params' last-resort fallback — the extraction cascade itself now
    calls through `build_router` instead of iterating this list directly."""
    settings = settings or Settings()
    return [dict(entry["litellm_params"]) for entry in build_model_list(settings)]
