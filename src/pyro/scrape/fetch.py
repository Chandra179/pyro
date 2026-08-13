"""Playwright-rendered HTML fetch (plan.md 'Raw Phase' — client-side rendering)."""

from __future__ import annotations

import asyncio
import logging
import random

from playwright.async_api import async_playwright

from pyro.db import Database
from pyro.urlkey import normalize_url

logger = logging.getLogger(__name__)

# A realistic desktop UA + a settle wait (instead of "networkidle", which never
# fires on pages with long-lived analytics/websocket connections, and which
# some hosts' bot-detection uses as a signal) avoids anti-bot challenge pages
# on Medium-hosted blogs.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_SETTLE_MS = 3000

# Bursty concurrent requests trip Cloudflare's bot challenge even with a
# realistic UA. These markers identify a challenge page so it can be retried
# instead of silently stored as if it were real article content.
_CHALLENGE_MARKERS = ("Attention Required! | Cloudflare", "Just a moment...", "cf-chl")
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_S = 8


async def scrape_urls(
    urls: list[str],
    db: Database,
    company_name: str,
    concurrency: int = 5,
    limit: int | None = None,
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

    sem = asyncio.Semaphore(concurrency)
    scraped_count = 0
    lock = asyncio.Lock()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        async def _fetch_one(url: str) -> None:
            nonlocal scraped_count
            async with sem:
                # Stagger request starts so a burst of `concurrency` new pages
                # doesn't itself look like the bot traffic Cloudflare flags.
                await asyncio.sleep(random.uniform(0, 1.5))

                title, html = None, None
                for attempt in range(1, _MAX_ATTEMPTS + 1):
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                        await page.wait_for_timeout(_SETTLE_MS)
                        title = await page.title()
                        html = await page.content()
                    except Exception:
                        logger.exception("failed to fetch %s (attempt %d)", url, attempt)
                        title, html = None, None
                    finally:
                        await page.close()

                    if html is not None and not any(marker in html for marker in _CHALLENGE_MARKERS):
                        break
                    logger.warning("challenge/blocked page for %s (attempt %d)", url, attempt)
                    title, html = None, None
                    if attempt < _MAX_ATTEMPTS:
                        await asyncio.sleep(_RETRY_BACKOFF_S * attempt)

                if html is None:
                    logger.error("giving up on %s after %d attempts", url, _MAX_ATTEMPTS)
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
