"""LiteLLM Router cascade config (plan.md 'Model Routing: The Free Provider First Model').

Tiers are included only when their env-var key is actually configured, so the
cascade degrades gracefully down to whatever provider(s) are available. Order:
  1-3. OpenRouter curated free models — verified live against
       https://openrouter.ai/api/v1/models to support `response_format`
       (needed for our JSON extraction calls). OpenRouter's free catalog
       rotates over time (e.g. gemini-2.5-flash:free, llama-3.3-70b-instruct:free
       and qwen-2.5-72b-instruct:free have all since become paid-only) — if
       these three ever go stale, `openrouter/free` below still catches it.
  4. `openrouter/free` — OpenRouter's own built-in meta-router that auto-picks
     a free model. Self-updating safety net against future catalog rotation.
  5. Groq Cloud free tier (llama-3.3-70b-versatile) — very fast, tight TPM limits.
  6. Direct Google AI Studio (Gemini) — bigger context window, 15 RPM / 1000 RPD.
  7. OpenCode Zen free aliases (opencode.ai/zen) — rotating "-free" promo
     model IDs (e.g. big-pickle, deepseek-v4-flash-free), same OpenAI-
     compatible passthrough shape as the TokenRouter tiers below, gated on
     opencode_api_key. No subscription required for these — OpenCode Zen is
     pay-as-you-go for its paid models, separate from the $10/mo "OpenCode Go"
     product, which this integration does not use.
  8. TokenRouter free aliases (api.tokenrouter.com) — rotating "-free" model
     IDs (e.g. qwen/qwen3.8-max-free) that TokenRouter itself promos as $0,
     capacity-constrained and not guaranteed to stay free. Same OpenAI-compatible
     passthrough as tier 9, gated on the same tokenrouter_api_key.
  9. TokenRouter paid (api.tokenrouter.com) — OpenAI-compatible multi-provider
     proxy, added via litellm's generic `openai/` + `api_base` passthrough
     rather than a bespoke client (litellm already abstracts "any OpenAI-shaped
     endpoint" — this is that abstraction point, not a new one). Sits after the
     free tiers, before the direct-OpenAI last resort.
  10. Paid fallback (OpenAI gpt-4o-mini) — last resort, no free-tier limits.
"""

from __future__ import annotations

from litellm import Router

from pyro.config import Settings


def build_model_list(settings: Settings) -> list[dict]:
    model_list: list[dict] = []

    if settings.openrouter_api_key:
        for model_name in settings.router.openrouter_free_models:
            model_list.append(
                {
                    "model_name": "extraction-cascade",
                    "litellm_params": {
                        "model": model_name,
                        "api_key": settings.openrouter_api_key,
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
                    },
                }
            )

    # if settings.tokenrouter_api_key:
    #     for model_name in settings.router.tokenrouter_free_models:
    #         model_list.append(
    #             {
    #                 "model_name": "extraction-cascade",
    #                 "litellm_params": {
    #                     "model": f"openai/{model_name}",
    #                     "api_base": settings.router.tokenrouter_api_base,
    #                     "api_key": settings.tokenrouter_api_key,
    #                 },
    #             }
    #         )
    #     model_list.append(
    #         {
    #             "model_name": "extraction-cascade",
    #             "litellm_params": {
    #                 "model": f"openai/{settings.router.tokenrouter_model}",
    #                 "api_base": settings.router.tokenrouter_api_base,
    #                 "api_key": settings.tokenrouter_api_key,
    #             },
    #         }
    #     )

    if settings.openai_api_key:
        model_list.append(
            {
                "model_name": "extraction-cascade",
                "litellm_params": {
                    "model": settings.router.openai_model,
                    "api_key": settings.openai_api_key,
                },
            }
        )

    return model_list


def build_router(settings: Settings | None = None) -> Router:
    settings = settings or Settings()
    model_list = build_model_list(settings)
    if not model_list:
        raise RuntimeError(
            "No LLM provider configured — set at least OPENROUTER_API_KEY."
        )
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

    Used by the extraction pipeline's schema-validation-retry loop, which needs
    to advance through concrete models directly (Router fallback only fires on
    raised exceptions, not on schema-invalid 200 OK responses).
    """
    settings = settings or Settings()
    return [entry["litellm_params"]["model"] for entry in build_model_list(settings)]


def synthesis_model_params(settings: Settings | None = None) -> dict:
    """Credentials for the single-shot high-reasoning synthesis pass.

    Uses `settings.synthesis_model` (an OpenRouter free model by default) when
    `OPENROUTER_API_KEY` is actually configured; otherwise falls back to the
    last (highest-capability / most-paid) tier in the extraction cascade, so
    synthesis still runs when only a subset of providers — e.g. just
    TokenRouter — is configured.
    """
    settings = settings or Settings()
    if settings.openrouter_api_key and settings.synthesis_model.startswith("openrouter/"):
        return {"model": settings.synthesis_model, "api_key": settings.openrouter_api_key}

    model_list = build_model_list(settings)
    if not model_list:
        raise RuntimeError("No LLM provider configured — set at least OPENROUTER_API_KEY.")
    return dict(model_list[-1]["litellm_params"])


def concrete_model_params(settings: Settings | None = None) -> list[dict]:
    """The ordered list of `litellm_params` dicts (model + credentials) in the cascade.

    Unlike `concrete_model_names`, this carries each tier's `api_key`/`api_base`
    so callers that bypass the `Router` object (see `extract/pipeline.py`) can
    still authenticate against tiers that aren't on litellm's default env-var
    naming convention — e.g. TokenRouter's `openai/` passthrough, which needs an
    explicit `api_base` and a non-`OPENAI_API_KEY` credential.
    """
    settings = settings or Settings()
    return [dict(entry["litellm_params"]) for entry in build_model_list(settings)]
