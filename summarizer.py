"""Ollama LLM summarizer: converts article text into a podcast-style prose script."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from config import Settings, load_settings

logger = logging.getLogger(__name__)


class SummarizerError(Exception):
    """Raised when the Ollama summarizer fails."""


@dataclass
class ArticleMetadata:
    """Title and description extracted from article text by the LLM."""
    title: str
    description: str


_METADATA_PROMPT = (
    "Extract a title and a 1-2 sentence description from the following article text. "
    "The title must be a complete phrase (not cut off mid-sentence), max 80 characters. "
    "The description must be 1-2 complete sentences, max 300 characters. "
    "Respond ONLY with valid JSON in this exact format, no other text:\n"
    '{"title": "...", "description": "..."}\n\n---\n\n'
)


def _call_ollama(prompt: str, settings: Settings, timeout: float = 300.0) -> str:
    """Send a prompt to Ollama and return the response text."""
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise SummarizerError("Ollama request timed out") from exc
    except httpx.RequestError as exc:
        raise SummarizerError(f"Cannot reach Ollama at {settings.ollama_url}: {exc}") from exc

    if response.status_code != 200:
        raise SummarizerError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
        result = data["response"]
    except (KeyError, ValueError) as exc:
        raise SummarizerError(
            f"Unexpected Ollama response format: {response.text[:200]}"
        ) from exc

    if not result or not result.strip():
        raise SummarizerError("Ollama returned an empty response")

    return result.strip()


def extract_metadata(text: str, settings: Settings | None = None) -> ArticleMetadata:
    """Ask the LLM to extract a title and short description from article text.

    Falls back to a heuristic if the LLM response can't be parsed.
    """
    if settings is None:
        settings = load_settings()

    prompt = _METADATA_PROMPT + text[:settings.max_input_chars]

    logger.info("Extracting title/description via Ollama")
    t0 = time.monotonic()

    try:
        raw = _call_ollama(prompt, settings, timeout=60.0)
    except SummarizerError:
        logger.warning("Metadata extraction failed — using fallback")
        return _fallback_metadata(text)

    elapsed = time.monotonic() - t0
    logger.info("Metadata extracted in %.1fs", elapsed)

    return _parse_metadata(raw, text)


def _truncate_to_sentence(text: str, max_chars: int) -> str:
    """Truncate *text* to at most *max_chars*, ending at the last full sentence.

    Looks for sentence-ending punctuation (.!?) followed by a space or end-of-string.
    Returns the text up through that punctuation mark (inclusive).
    If no sentence boundary exists within the limit, truncates to the last word boundary
    and appends an ellipsis.
    """
    import re

    text = text.strip()
    if len(text) <= max_chars:
        return text

    window = text[:max_chars]

    # Find the last sentence-ending punctuation within the window.
    # Match .!? that are followed by a space, end-of-string, or a quote char.
    last_end = None
    for m in re.finditer(r'[.!?]', window):
        pos = m.end()  # position right after the punctuation
        # Accept if it's at the end of the window or followed by whitespace
        if pos >= len(window) or window[pos] in (' ', '\n', '\t', '"', "'"):
            last_end = pos

    if last_end and last_end >= 10:
        return window[:last_end].strip()

    # No good sentence boundary — truncate at last word boundary
    truncated = window.rsplit(" ", 1)[0]
    return truncated.rstrip(",:;-–— ") + "…"


def _parse_metadata(raw: str, text: str) -> ArticleMetadata:
    """Parse LLM JSON response into ArticleMetadata, with fallback."""
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        title = _truncate_to_sentence(str(data.get("title", "")).strip(), 80)
        description = _truncate_to_sentence(str(data.get("description", "")).strip(), 300)
        if title:
            return ArticleMetadata(title=title, description=description)
    except (json.JSONDecodeError, AttributeError):
        pass

    logger.debug("Could not parse metadata JSON, using fallback")
    return _fallback_metadata(text)


def _fallback_metadata(text: str) -> ArticleMetadata:
    """Derive title/description from raw text when LLM extraction fails."""
    first_line = text.strip().split("\n")[0].strip()
    title = _truncate_to_sentence(first_line, 80) if first_line else "Untitled"
    description = _truncate_to_sentence(text.strip(), 200)
    return ArticleMetadata(title=title, description=description)


def summarize(text: str, settings: Settings | None = None) -> str:
    """Send *text* to Ollama and return a conversational podcast script.

    The returned script contains plain prose — no markdown, bullet points,
    or headers — suitable for direct TTS synthesis.

    Raises:
        SummarizerError: on HTTP error, timeout, or unexpected Ollama response.
    """
    if settings is None:
        settings = load_settings()

    prompt = f"{settings.ollama_prompt}\n\n---\n\n{text}"

    logger.info(
        "Sending %d chars to Ollama model=%s at %s",
        len(text),
        settings.ollama_model,
        settings.ollama_url,
    )
    t0 = time.monotonic()

    script = _call_ollama(prompt, settings)

    elapsed = time.monotonic() - t0
    logger.info("Ollama generated %d chars in %.1fs", len(script), elapsed)
    return script
