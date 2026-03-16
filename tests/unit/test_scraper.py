"""Unit tests for scraper.py."""
import pytest
import respx
import httpx

from config import Settings
from scraper import ScraperError, scrape, _extract_thumbnail


ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Article</title></head>
<body>
  <nav>Navigation stuff that should be stripped</nav>
  <article>
    <h1>The Main Article Title</h1>
    <p>This is the first paragraph of the article with meaningful content.</p>
    <p>Here is a second paragraph with more interesting information about the topic.</p>
  </article>
  <footer>Footer boilerplate</footer>
</body>
</html>
"""

ARTICLE_WITH_OG = """
<!DOCTYPE html>
<html>
<head>
  <title>Test</title>
  <meta property="og:image" content="https://example.com/og-image.jpg">
</head>
<body>
  <article>
    <p>This is the first paragraph of the article with meaningful content.</p>
    <p>Here is a second paragraph with more interesting information about the topic.</p>
  </article>
</body>
</html>
"""


def _settings(**kwargs) -> Settings:
    s = Settings()
    s.scrape_timeout_sec = 5
    s.max_input_tokens = 4096
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


@respx.mock
def test_scrape_returns_text():
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, text=ARTICLE_HTML)
    )
    result = scrape("https://example.com/article", _settings())
    assert "paragraph" in result.text.lower()
    assert "Navigation stuff" not in result.text
    assert "Footer boilerplate" not in result.text


@respx.mock
def test_scrape_extracts_og_image():
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, text=ARTICLE_WITH_OG)
    )
    result = scrape("https://example.com/article", _settings())
    assert result.thumbnail_url == "https://example.com/og-image.jpg"


@respx.mock
def test_scrape_no_thumbnail_returns_empty():
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, text=ARTICLE_HTML)
    )
    result = scrape("https://example.com/article", _settings())
    assert result.thumbnail_url == ""


@respx.mock
def test_scrape_http_error_raises():
    respx.get("https://example.com/notfound").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(ScraperError, match="HTTP 404"):
        scrape("https://example.com/notfound", _settings())


@respx.mock
def test_scrape_500_raises():
    respx.get("https://example.com/error").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(ScraperError, match="HTTP 500"):
        scrape("https://example.com/error", _settings())


@respx.mock
def test_scrape_timeout_raises():
    respx.get("https://example.com/slow").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(ScraperError, match="timed out"):
        scrape("https://example.com/slow", _settings())


@respx.mock
def test_scrape_network_error_raises():
    respx.get("https://example.com/unreachable").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(ScraperError, match="Network error"):
        scrape("https://example.com/unreachable", _settings())


@respx.mock
def test_scrape_truncates_long_content():
    long_content = "<p>" + "word " * 10_000 + "</p>"
    html = f"<html><body><article>{long_content}</article></body></html>"
    respx.get("https://example.com/long").mock(
        return_value=httpx.Response(200, text=html)
    )
    result = scrape("https://example.com/long", _settings(max_input_tokens=10))
    assert len(result.text) <= 40


@respx.mock
def test_scrape_empty_content_raises():
    respx.get("https://example.com/empty").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    with pytest.raises(ScraperError, match="extract"):
        scrape("https://example.com/empty", _settings())


# ---------------------------------------------------------------------------
# _extract_thumbnail tests
# ---------------------------------------------------------------------------

def test_extract_thumbnail_og_image():
    html = '<meta property="og:image" content="https://img.example.com/photo.jpg">'
    assert _extract_thumbnail(html, "https://example.com") == "https://img.example.com/photo.jpg"


def test_extract_thumbnail_og_image_reversed_attrs():
    html = '<meta content="/img/photo.png" property="og:image">'
    assert _extract_thumbnail(html, "https://example.com") == "https://example.com/img/photo.png"


def test_extract_thumbnail_twitter_image():
    html = '<meta name="twitter:image" content="https://img.example.com/tw.jpg">'
    assert _extract_thumbnail(html, "https://example.com") == "https://img.example.com/tw.jpg"


def test_extract_thumbnail_relative_url():
    html = '<meta property="og:image" content="/images/cover.jpg">'
    assert _extract_thumbnail(html, "https://example.com/article") == "https://example.com/images/cover.jpg"


def test_extract_thumbnail_none_found():
    html = "<html><head><title>No images</title></head></html>"
    assert _extract_thumbnail(html, "https://example.com") == ""
