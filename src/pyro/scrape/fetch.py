"""Playwright-rendered HTML fetch (docs/architecture.md, "The layers" — Ingestion; client-side rendering)."""

from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import Page, async_playwright
from tenacity import retry_if_exception_type, stop_after_attempt, wait_incrementing
from tenacity.asyncio import AsyncRetrying

from pyro.config import ScrapeConfig
from pyro.db import Database
from pyro.urlkey import normalize_url

logger = logging.getLogger(__name__)

_DEFAULT_SCRAPE_CONFIG = ScrapeConfig()


class _ChallengePageError(Exception):
    """A fetched page looks like a bot-detection challenge; reuses the network-error retry loop."""


async def _fetch_page_once(
    context, url: str, config: ScrapeConfig
) -> tuple[str | None, str]:
    page: Page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=config.page_goto_timeout_ms)
        await page.wait_for_timeout(config.settle_ms)
        title = await page.title()
        html = await page.content()
    finally:
        await page.close()

    if any(marker in html for marker in config.challenge_markers):
        raise _ChallengePageError(f"challenge/blocked page for {url}")
    return title, html


async def scrape_urls(
    urls: list[str],
    db: Database,
    company_name: str,
    concurrency: int | None = None,
    limit: int | None = None,
    config: ScrapeConfig = _DEFAULT_SCRAPE_CONFIG,
) -> int:
    """Render each URL with Playwright and store raw HTML in ArangoDB.

    Skips URLs whose normalized id already has a row (dedup guarantee).
    Returns the number of newly scraped articles.
    """
    # One batched existence check for the whole sitemap instead of one round-trip per URL — matters
    # once a company's history is years of posts, since this runs serially before any concurrent
    # fetching starts.
    ids_by_url = {u: normalize_url(u) for u in urls}
    already_scraped = db.existing_ids(list(ids_by_url.values()))
    pending = [u for u, article_id in ids_by_url.items() if article_id not in already_scraped]
    if limit is not None:
        pending = pending[:limit]

    if not pending:
        return 0

    sem = asyncio.Semaphore(
        concurrency if concurrency is not None else config.concurrency
    )
    scraped_count = 0
    lock = asyncio.Lock()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=config.user_agent,
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            locale=config.locale,
        )

        async def _fetch_one(url: str) -> None:
            nonlocal scraped_count
            async with sem:
                # Stagger request starts so a burst of `concurrency` new pages
                # doesn't itself look like the bot traffic Cloudflare flags.
                await asyncio.sleep(random.uniform(0, 1.5))

                title, html = None, None
                try:
                    async for retry_attempt in AsyncRetrying(
                        stop=stop_after_attempt(config.max_attempts),
                        wait=wait_incrementing(
                            start=config.retry_backoff_s,
                            increment=config.retry_backoff_s,
                        ),
                        retry=retry_if_exception_type(Exception),
                        before_sleep=lambda rs: logger.warning(
                            "fetch failed for %s (attempt %d/%d): %s",
                            url,
                            rs.attempt_number,
                            config.max_attempts,
                            rs.outcome.exception(),
                        ),
                    ):
                        with retry_attempt:
                            title, html = await _fetch_page_once(context, url, config)
                except Exception:
                    title, html = None, None

                if html is None:
                    logger.error(
                        "giving up on %s after %d attempts", url, config.max_attempts
                    )
                    return

            article_id = normalize_url(url)
            db.upsert_raw(
                id=article_id,
                source_url=url,
                title=title,
                company_name=company_name,
                raw_html=html,
            )
            async with lock:
                scraped_count += 1

        await asyncio.gather(*(_fetch_one(u) for u in pending))
        await context.close()
        await browser.close()

    return scraped_count
