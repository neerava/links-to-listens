"""Unit tests for summarizer.py."""
import json
import pytest
import respx
import httpx

from config import Settings
from summarizer import SummarizerError, summarize, extract_metadata, _parse_metadata, _fallback_metadata, _truncate_to_sentence


def _settings(**kwargs) -> Settings:
    s = Settings()
    s.ollama_url = "http://localhost:11434"
    s.ollama_model = "llama3"
    s.ollama_prompt = "Convert this to a podcast script."
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _ollama_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"model": "llama3", "response": text, "done": True},
    )


PROSE_SCRIPT = (
    "Welcome to today's episode where we explore the fascinating world of technology. "
    "Our story begins with an interesting development in the field of artificial intelligence. "
    "Scientists have discovered a new approach that promises to change everything."
)


@respx.mock
def test_summarize_returns_script():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response(PROSE_SCRIPT)
    )
    result = summarize("Some article text", _settings())
    assert result == PROSE_SCRIPT


@respx.mock
def test_summarize_sends_correct_payload():
    request_body = {}

    def capture(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        request_body = json.loads(request.content)
        return _ollama_response(PROSE_SCRIPT)

    respx.post("http://localhost:11434/api/generate").mock(side_effect=capture)
    summarize("article text", _settings())

    assert request_body["model"] == "llama3"
    assert request_body["stream"] is False
    assert "article text" in request_body["prompt"]


@respx.mock
def test_summarize_http_error_raises():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(SummarizerError, match="HTTP 500"):
        summarize("text", _settings())


@respx.mock
def test_summarize_network_error_raises():
    respx.post("http://localhost:11434/api/generate").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(SummarizerError, match="Cannot reach Ollama"):
        summarize("text", _settings())


@respx.mock
def test_summarize_missing_response_field_raises():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"done": True})  # missing "response"
    )
    with pytest.raises(SummarizerError, match="Unexpected Ollama response"):
        summarize("text", _settings())


@respx.mock
def test_summarize_empty_response_raises():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response("   ")
    )
    with pytest.raises(SummarizerError, match="empty"):
        summarize("text", _settings())


@respx.mock
def test_summarize_strips_whitespace():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response("  Hello world.  ")
    )
    result = summarize("text", _settings())
    assert result == "Hello world."


# ---------------------------------------------------------------------------
# extract_metadata tests
# ---------------------------------------------------------------------------

@respx.mock
def test_extract_metadata_parses_json():
    llm_json = '{"title": "My Great Article", "description": "A deep dive into AI."}'
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response(llm_json)
    )
    meta = extract_metadata("Some article text", _settings())
    assert meta.title == "My Great Article"
    assert meta.description == "A deep dive into AI."


@respx.mock
def test_extract_metadata_handles_code_fences():
    llm_json = '```json\n{"title": "Fenced Title", "description": "Fenced desc."}\n```'
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response(llm_json)
    )
    meta = extract_metadata("Some article text", _settings())
    assert meta.title == "Fenced Title"
    assert meta.description == "Fenced desc."


@respx.mock
def test_extract_metadata_fallback_on_bad_json():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=_ollama_response("This is not JSON at all")
    )
    meta = extract_metadata("First line of the article. More text here.", _settings())
    # Falls back to first line as title
    assert meta.title == "First line of the article. More text here."


@respx.mock
def test_extract_metadata_fallback_on_network_error():
    respx.post("http://localhost:11434/api/generate").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    meta = extract_metadata("Article title here. Some body text.", _settings())
    assert meta.title == "Article title here. Some body text."


def test_parse_metadata_valid_json():
    raw = '{"title": "Test", "description": "Desc"}'
    meta = _parse_metadata(raw, "fallback text")
    assert meta.title == "Test"
    assert meta.description == "Desc"


def test_parse_metadata_truncates_long_title_at_sentence():
    # Title: 16 chars for "This is a title." then a very long second sentence
    long_second = "Then it " + "really " * 20 + "keeps going."
    raw = json.dumps({"title": "This is a title. " + long_second, "description": "Short."})
    meta = _parse_metadata(raw, "fallback")
    assert len(meta.title) <= 81  # may have ellipsis
    assert meta.title == "This is a title."


def test_parse_metadata_truncates_at_word_boundary():
    raw = json.dumps({"title": "Word " * 30, "description": "Short."})
    meta = _parse_metadata(raw, "fallback")
    assert len(meta.title) <= 81  # +1 for the … character
    assert not meta.title.endswith(" ")  # no trailing space


def test_parse_metadata_falls_back_on_empty_title():
    raw = '{"title": "", "description": "Desc"}'
    meta = _parse_metadata(raw, "Fallback line. More.")
    # "Fallback line. More." is 20 chars — fits in 80 limit, returned as-is
    assert meta.title == "Fallback line. More."


def test_fallback_metadata_uses_first_line():
    text = "This is the headline\nMore content follows."
    meta = _fallback_metadata(text)
    assert meta.title == "This is the headline"


# ---------------------------------------------------------------------------
# _truncate_to_sentence tests
# ---------------------------------------------------------------------------

def test_truncate_short_text_unchanged():
    assert _truncate_to_sentence("Short text.", 80) == "Short text."


def test_truncate_at_sentence_boundary():
    text = "First sentence. Second sentence is much longer and goes on and on and on."
    result = _truncate_to_sentence(text, 30)
    assert result == "First sentence."


def test_truncate_at_word_boundary_when_no_sentence():
    text = "This is a long phrase with no period that keeps going and going"
    result = _truncate_to_sentence(text, 30)
    assert len(result) <= 31  # +1 for …
    assert result.endswith("…")
    assert "  " not in result


def test_truncate_preserves_exclamation_and_question():
    text = "Is this good? I think so. More text follows after this point and keeps on going."
    result = _truncate_to_sentence(text, 28)
    assert result == "Is this good? I think so."
