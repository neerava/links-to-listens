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


def scrape(url: str, settings: Settings | None = None) -> ScrapeResult:
    """Fetch *url* and return cleaned article text.

    Strips navigation, ads, and boilerplate using trafilatura.
    Truncates output to ``settings.max_input_chars`` characters.

    Raises:
        ScraperError: on network error, timeout, HTTP error, or empty extraction.
    """
    if settings is None:
        settings = load_settings()

    logger.info("Scraping URL: %s", url)

    try:
        response = httpx.get(
            url,
            timeout=settings.scrape_timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": "url-to-podcast/1.0 (content summarizer)"},
        )
    except httpx.TimeoutException as exc:
        raise ScraperError(f"Request timed out after {settings.scrape_timeout_sec}s: {url}") from exc
    except httpx.RequestError as exc:
        raise ScraperError(f"Network error fetching {url}: {exc}") from exc

    if response.status_code >= 400:
        raise ScraperError(
            f"HTTP {response.status_code} fetching {url}"
        )

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
