"""Wrappers around a single litellm acompletion() call that add provider-level rate-limit
retry — separate from router/cascade.py's job of deciding *which* model/credentials to call
with; these just make one such call resilient, blocking or streaming."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from litellm.exceptions import RateLimitError
from tenacity import retry_if_exception_type, stop_after_attempt, wait_fixed
from tenacity.asyncio import AsyncRetrying

from pyro.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _log_rate_limit_retry(settings: Settings, retry_state) -> None:
    logger.warning(
        "rate limited (attempt %d/%d) — waiting %ds: %s",
        retry_state.attempt_number,
        settings.router.rate_limit_max_retries,
        settings.router.rate_limit_wait_seconds,
        retry_state.outcome.exception(),
    )


async def call_with_rate_limit_retry(
    fn: Callable[[], Awaitable[T]], settings: Settings
) -> T:
    """Retry fn() (one acompletion call) on RateLimitError, waiting router.rate_limit_wait_seconds
    between attempts. Needed because a provider-level throttle (e.g. TokenRouter's "Maximum 1
    requests within 1 minutes") can be far tighter than the cascade's own num_retries/cooldown_time,
    which only governs advancing to the *next* tier — no help when the rate-limited tier is the
    only one configured."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(settings.router.rate_limit_max_retries),
        wait=wait_fixed(settings.router.rate_limit_wait_seconds),
        retry=retry_if_exception_type(RateLimitError),
        before_sleep=lambda retry_state: _log_rate_limit_retry(settings, retry_state),
        reraise=True,
    ):
        with attempt:
            return await fn()
    raise AssertionError("unreachable — AsyncRetrying always returns or raises")


async def stream_with_rate_limit_retry(
    fn: Callable[[], Awaitable[T]],
    settings: Settings,
    on_chunk: Callable[[str, str], None],
) -> tuple[str, str]:
    """Streaming sibling of call_with_rate_limit_retry: fn() must return an acompletion(...,
    stream=True) call. Calls on_chunk(content_piece, reasoning_piece) as each chunk arrives —
    reasoning_piece is only ever non-empty on models that expose extended-thinking/reasoning
    traces, empty string otherwise — and returns the full (content, reasoning) once the stream
    ends. Only retries a RateLimitError raised before the first chunk arrives: a stream that
    fails partway through can't be replayed without duplicating whatever on_chunk already saw,
    so a mid-stream failure of any kind propagates immediately instead of retrying."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(settings.router.rate_limit_max_retries),
        wait=wait_fixed(settings.router.rate_limit_wait_seconds),
        retry=retry_if_exception_type(RateLimitError),
        before_sleep=lambda retry_state: _log_rate_limit_retry(settings, retry_state),
        reraise=True,
    ):
        with attempt:
            stream = await fn()
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            async for chunk in stream:
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None) or ""
                reasoning_piece = getattr(delta, "reasoning_content", None) or ""
                if piece or reasoning_piece:
                    content_parts.append(piece)
                    reasoning_parts.append(reasoning_piece)
                    on_chunk(piece, reasoning_piece)
            return "".join(content_parts), "".join(reasoning_parts)
    raise AssertionError("unreachable — AsyncRetrying always returns or raises")
