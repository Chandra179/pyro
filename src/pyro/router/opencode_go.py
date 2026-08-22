"""Direct (non-litellm) client for OpenCode Go's OpenAI-compatible chat-completions endpoint.

litellm's acompletion() misclassified OpenCode Go's responses as RateLimitError even at 0% usage
in OpenCode's own 5-hour window, so this bypasses litellm entirely: a plain httpx POST with our
own 429 handling. Opt-in via `router.opencode_go_enabled`, tried after the litellm cascade fails.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt, wait_fixed
from tenacity.asyncio import AsyncRetrying

from pyro.config import Settings

logger = logging.getLogger(__name__)


class OpenCodeGoError(RuntimeError):
    """Non-2xx response from OpenCode Go, or a malformed response body."""


class OpenCodeGoRateLimited(OpenCodeGoError):
    """A genuine 429 from OpenCode Go — retried with the long provider-rate-limit backoff."""


class OpenCodeGoServerError(OpenCodeGoError):
    """A 5xx from OpenCode Go — observed to be an intermittent blip (identical retries usually
    succeed within seconds), not a real outage, so retried fast rather than at 429's backoff."""


# 5xx blips clear almost immediately on retry (observed), unlike a real rate limit — short, fast
# attempts rather than router.rate_limit_wait_seconds' 65s.
_SERVER_ERROR_MAX_ATTEMPTS = 3
_SERVER_ERROR_WAIT_SECONDS = 3


async def _post(
    messages: list[dict],
    model: str,
    settings: Settings,
    max_tokens: int,
    decoding_params: dict | None,
    response_format: dict | None,
) -> str:
    payload: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if decoding_params:
        payload.update(decoding_params)
    if response_format is not None:
        payload["response_format"] = response_format

    url = f"{settings.router.opencode_go_api_base.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=settings.router.timeout) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.opencode_api_key}"},
            json=payload,
        )
    if resp.status_code == 429:
        raise OpenCodeGoRateLimited(f"opencode go 429: {resp.text[:500]}")
    if resp.status_code >= 500:
        raise OpenCodeGoServerError(f"opencode go {resp.status_code}: {resp.text[:500]}")
    if resp.status_code >= 400:
        raise OpenCodeGoError(f"opencode go {resp.status_code}: {resp.text[:500]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise OpenCodeGoError(f"opencode go: unexpected response shape: {resp.text[:500]}") from exc


async def _post_with_server_error_retry(
    messages: list[dict],
    model: str,
    settings: Settings,
    max_tokens: int,
    decoding_params: dict | None,
    response_format: dict | None,
) -> str:
    def _log_retry(retry_state) -> None:
        logger.warning(
            "opencode go server error (attempt %d/%d) — retrying in %ds",
            retry_state.attempt_number,
            _SERVER_ERROR_MAX_ATTEMPTS,
            _SERVER_ERROR_WAIT_SECONDS,
        )

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(_SERVER_ERROR_MAX_ATTEMPTS),
        wait=wait_fixed(_SERVER_ERROR_WAIT_SECONDS),
        retry=retry_if_exception_type(OpenCodeGoServerError),
        before_sleep=_log_retry,
        reraise=True,
    ):
        with attempt:
            return await _post(
                messages, model, settings, max_tokens, decoding_params, response_format
            )
    raise AssertionError("unreachable — AsyncRetrying always returns or raises")


async def call_opencode_go_direct(
    messages: list[dict],
    settings: Settings,
    *,
    max_tokens: int,
    decoding_params: dict | None = None,
    response_format: dict | None = None,
) -> str:
    """One call to OpenCode Go's configured model, bypassing litellm entirely. A genuine 429
    retries with the same long backoff as the litellm-backed tiers
    (router.rate_limit_max_retries/rate_limit_wait_seconds); an intermittent 5xx retries fast
    (see OpenCodeGoServerError)."""
    model = settings.router.opencode_go_model

    def _log_rate_limit_retry(retry_state) -> None:
        logger.warning(
            "opencode go rate limited (attempt %d/%d) — waiting %ds",
            retry_state.attempt_number,
            settings.router.rate_limit_max_retries,
            settings.router.rate_limit_wait_seconds,
        )

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(settings.router.rate_limit_max_retries),
        wait=wait_fixed(settings.router.rate_limit_wait_seconds),
        retry=retry_if_exception_type(OpenCodeGoRateLimited),
        before_sleep=_log_rate_limit_retry,
        reraise=True,
    ):
        with attempt:
            return await _post_with_server_error_retry(
                messages, model, settings, max_tokens, decoding_params, response_format
            )
    raise AssertionError("unreachable — AsyncRetrying always returns or raises")
