"""Integration tests for the watcher orchestrator."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from config import Settings
from metadata import MetadataStore
from models import Episode
import watcher as watcher_mod
from script_api import ScriptResult
from watcher import process_url, _run_once, _slugify, _build_audio_filename


def _settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.output_path = tmp_path
    s.tts_voice = "default"
    s.ollama_model = "llama3"
    s.ollama_url = "http://localhost:11434"
    s.ollama_prompt = "Make a podcast."
    s.scrape_timeout_sec = 5
    s.max_input_tokens = 4096
    return s


SCRIPT = "Welcome to the podcast. Today we talk about interesting things in technology."


@pytest.fixture(autouse=True)
def _clear_failed_urls():
    """Reset the failed-URL set between tests."""
    watcher_mod._failed_urls.clear()
    yield
    watcher_mod._failed_urls.clear()


def test_process_url_full_pipeline(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    fake_script_result = ScriptResult(
        title="Test Article Title",
        description="A short description.",
        thumbnail_url="https://example.com/img.jpg",
        script=SCRIPT,
    )

    def fake_generate_audio(script, output_path, settings):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKE_MP3")
        return output_path

    with patch("watcher.generate_script", return_value=fake_script_result), \
         patch("watcher.generate_audio", side_effect=fake_generate_audio):
        process_url("https://example.com/article", settings, store)

    episodes = store.load()
    assert len(episodes) == 1
    assert episodes[0].source_url == "https://example.com/article"
    assert episodes[0].title == "Test Article Title"
    assert episodes[0].description == "A short description."
    assert episodes[0].thumbnail_url == "https://example.com/img.jpg"
    assert episodes[0].audio_path.endswith(".mp3")


def test_process_url_scrape_error_does_not_crash(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    from scraper import ScraperError
    with patch("watcher.generate_script", side_effect=ScraperError("timeout")):
        process_url("https://example.com/bad", settings, store)

    assert store.load() == []


def test_process_url_summarizer_error_does_not_crash(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    from summarizer import SummarizerError
    with patch("watcher.generate_script", side_effect=SummarizerError("ollama down")):
        process_url("https://example.com/bad", settings, store)

    assert store.load() == []


def test_process_url_tts_error_does_not_crash(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    from tts import TTSError
    fake_script_result = ScriptResult(
        title="T", description="", thumbnail_url="", script=SCRIPT
    )
    with patch("watcher.generate_script", return_value=fake_script_result), \
         patch("watcher.generate_audio", side_effect=TTSError("vibevoice not found")):
        process_url("https://example.com/bad", settings, store)

    assert store.load() == []


def test_run_once_skips_already_processed(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    # Pre-populate store with a URL
    ep = Episode(
        id="existing",
        title="Already done",
        source_url="https://example.com/1",
        timestamp="2026-03-15T00:00:00+00:00",
        audio_path="already-done.mp3",
    )
    store.append(ep)

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/1\n")

    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.process_url") as mock_process:
        _run_once(settings, store)

    mock_process.assert_not_called()


def test_run_once_processes_new_url(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/new\n")

    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.process_url") as mock_process:
        _run_once(settings, store)

    mock_process.assert_called_once_with("https://example.com/new", settings, store)


def test_run_once_handles_empty_urls_file(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("")

    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.process_url") as mock_process:
        _run_once(settings, store)

    mock_process.assert_not_called()


def test_run_once_ignores_comment_lines(tmp_path):
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("# This is a comment\n\nhttps://example.com/real\n")

    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.process_url") as mock_process:
        _run_once(settings, store)

    mock_process.assert_called_once_with("https://example.com/real", settings, store)


def test_run_once_skips_failed_urls(tmp_path):
    """After a URL fails, it should not be retried on the next poll cycle."""
    settings = _settings(tmp_path)
    store = MetadataStore(tmp_path / "metadata.json")

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/broken\n")

    from scraper import ScraperError
    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.generate_script", side_effect=ScraperError("timeout")):
        process_url("https://example.com/broken", settings, store)

    # URL is now in _failed_urls — _run_once should skip it
    with patch("watcher.URLS_FILE", urls_file), \
         patch("watcher.process_url") as mock_process:
        _run_once(settings, store)

    mock_process.assert_not_called()


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def test_slugify():
    assert _slugify("Hello World! 2026") == "hello-world-2026"
    assert _slugify("  spaces  ") == "spaces"


def test_build_audio_filename():
    name = _build_audio_filename("Test Title")
    assert name.endswith(".mp3")
    assert "test-title" in name
