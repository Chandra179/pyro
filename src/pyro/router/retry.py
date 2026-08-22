"""Rate-limit retry for a single streaming litellm acompletion() call — separate from
router/cascade.py's job of deciding *which* model/credentials to call with. Non-streaming calls go
through litellm's own Router (build_router) instead, whose num_retries/cooldown_time/allowed_fails
already cover this; graph/merge.py's single fixed-model streaming pass doesn't use Router."""

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


async def stream_with_rate_limit_retry(
    fn: Callable[[], Awaitable[T]],
    settings: Settings,
    on_chunk: Callable[[str, str], None],
) -> tuple[str, str]:
    """fn() must return an acompletion(..., stream=True) call. Calls on_chunk(content_piece,
    reasoning_piece) as each chunk arrives and returns the full (content, reasoning) once the
    stream ends. Only retries a RateLimitError before the first chunk — a stream failing partway
    through can't be replayed without duplicating what on_chunk already saw, so any mid-stream
    failure propagates immediately."""
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
