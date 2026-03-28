"""Web scraper: fetches a URL and extracts the main article text."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
import trafilatura

from config import Settings, load_settings

logger = logging.getLogger(__name__)


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _playwright_available() -> bool:
    """Check if Playwright is installed without importing it."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class ScraperError(Exception):
    """Raised when scraping fails for any reason."""


@dataclass
class ScrapeResult:
    """Article text and optional thumbnail URL extracted from a page."""
    text: str
    thumbnail_url: str  # empty string if none found


def _extract_thumbnail(html: str, page_url: str) -> str:
    """Extract the best thumbnail image URL from HTML meta tags.

    Priority: og:image > twitter:image > first large <img>.
    Returns an absolute URL or empty string.
    """
    # Open Graph image
    match = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html, re.IGNORECASE,
        )
    if match:
        return urljoin(page_url, match.group(1))

    # Twitter card image
    match = re.search(
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
            html, re.IGNORECASE,
        )
    if match:
        return urljoin(page_url, match.group(1))

    return ""


def _fetch_with_httpx(url: str, settings: Settings) -> httpx.Response:
    """Fetch a URL with httpx and browser-like headers."""
    try:
        return httpx.get(
            url,
            timeout=settings.scrape_timeout_sec,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
    except httpx.TimeoutException as exc:
        raise ScraperError(f"Request timed out after {settings.scrape_timeout_sec}s: {url}") from exc
    except httpx.RequestError as exc:
        raise ScraperError(f"Network error fetching {url}: {exc}") from exc


def _fetch_with_playwright(url: str, settings: Settings) -> str:
    """Fetch a URL using a headless Chromium browser. Returns raw HTML."""
    from playwright.sync_api import sync_playwright

    logger.info("Retrying with Playwright (headless browser): %s", url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
            timeout_ms = max(settings.scrape_timeout_sec, 30) * 1000
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Wait for JS-rendered content to settle
            page.wait_for_timeout(5000)
            html = page.content()
            browser.close()
    except Exception as exc:
        raise ScraperError(f"Playwright failed fetching {url}: {exc}") from exc

    return html


def scrape(url: str, settings: Settings | None = None) -> ScrapeResult:
    """Fetch *url* and return cleaned article text.

    Strips navigation, ads, and boilerplate using trafilatura.
    Truncates output to ``settings.max_input_chars`` characters.

    On HTTP 403, retries with Playwright (headless browser) if installed.

    Raises:
        ScraperError: on network error, timeout, HTTP error, or empty extraction.
    """
    if settings is None:
        settings = load_settings()

    logger.info("Scraping URL: %s", url)

    # Try httpx first (fast path)
    response = _fetch_with_httpx(url, settings)

    if response.status_code == 403 and _playwright_available():
        raw_html = _fetch_with_playwright(url, settings)
    elif response.status_code >= 400:
        hint = ""
        if response.status_code == 403 and not _playwright_available():
            hint = " (install Playwright for JS-protected sites: pip install playwright && playwright install chromium)"
        raise ScraperError(
            f"HTTP {response.status_code} fetching {url}{hint}"
        )
    else:
        raw_html = response.text

    # Extract thumbnail before stripping HTML
    thumbnail_url = _extract_thumbnail(raw_html, url)
    if thumbnail_url:
        logger.debug("Thumbnail found: %s", thumbnail_url)

    text = trafilatura.extract(
        raw_html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )

    if not text or not text.strip():
        raise ScraperError(f"Could not extract main content from {url}")

    text = text.strip()

    if len(text) > settings.max_input_chars:
        logger.debug(
            "Content truncated from %d to %d chars for %s",
            len(text),
            settings.max_input_chars,
            url,
        )
        text = text[: settings.max_input_chars]

    logger.info("Scraped %d chars from %s", len(text), url)
    return ScrapeResult(text=text, thumbnail_url=thumbnail_url)
