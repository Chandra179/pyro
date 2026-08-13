"""Playwright-rendered HTML fetch (plan.md 'Raw Phase' — client-side rendering)."""

from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import async_playwright

from pyro.config import ScrapeConfig
from pyro.db import Database
from pyro.urlkey import normalize_url

logger = logging.getLogger(__name__)

_DEFAULT_SCRAPE_CONFIG = ScrapeConfig()


async def scrape_urls(
    urls: list[str],
    db: Database,
    company_name: str,
    concurrency: int | None = None,
    limit: int | None = None,
    config: ScrapeConfig = _DEFAULT_SCRAPE_CONFIG,
) -> int:
    """Render each URL with Playwright and store raw HTML in SQLite.

    Skips URLs whose normalized id already has a row (dedup guarantee).
    Returns the number of newly scraped articles.
    """
    pending = [u for u in urls if not db.exists(normalize_url(u))]
    if limit is not None:
        pending = pending[:limit]

    if not pending:
        return 0

    sem = asyncio.Semaphore(concurrency if concurrency is not None else config.concurrency)
    scraped_count = 0
    lock = asyncio.Lock()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
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
                for attempt in range(1, config.max_attempts + 1):
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                        await page.wait_for_timeout(config.settle_ms)
                        title = await page.title()
                        html = await page.content()
                    except Exception:
                        logger.exception("failed to fetch %s (attempt %d)", url, attempt)
                        title, html = None, None
                    finally:
                        await page.close()

                    if html is not None and not any(marker in html for marker in config.challenge_markers):
                        break
                    logger.warning("challenge/blocked page for %s (attempt %d)", url, attempt)
                    title, html = None, None
                    if attempt < config.max_attempts:
                        await asyncio.sleep(config.retry_backoff_s * attempt)

                if html is None:
                    logger.error("giving up on %s after %d attempts", url, config.max_attempts)
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
