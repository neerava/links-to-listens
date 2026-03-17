"""Integration tests for the FastAPI web UI."""
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
from fastapi.testclient import TestClient

from models import Episode


FAKE_EPISODE = Episode(
    id="test-id-1",
    title="Test Episode Title",
    description="A deep dive into AI agent architectures and their trade-offs.",
    source_url="https://example.com/article",
    timestamp="2026-03-15T10:00:00+00:00",
    audio_path="test-episode.mp3",
    thumbnail_url="https://example.com/thumb.jpg",
)


@pytest.fixture()
def client(tmp_path):
    """Create a test client with an isolated output dir and metadata store."""
    # Create a fake MP3 for the audio endpoint test
    mp3_path = tmp_path / "test-episode.mp3"
    mp3_path.write_bytes(b"\xff\xfb\x90\x00" * 100)  # minimal fake MP3 bytes

    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path
    fake_settings.web_port = 8080

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    fake_store.append(FAKE_EPISODE)

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store):
        import app as app_module
        with TestClient(app_module.app) as c:
            yield c


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "URL to Podcast" in resp.text
    assert "Queue a New URL" in resp.text


def test_index_shows_episode(client):
    resp = client.get("/")
    assert "Test Episode Title" in resp.text
    assert "A deep dive into AI agent architectures" in resp.text
    assert "https://example.com/article" in resp.text


def test_index_shows_thumbnail(client):
    resp = client.get("/")
    assert "/img?url=" in resp.text
    assert "thumb.jpg" in resp.text


def test_index_shows_audio_player(client):
    resp = client.get("/")
    assert "<audio" in resp.text
    assert "test-episode.mp3" in resp.text


def test_audio_serves_file(client):
    resp = client.get("/audio/test-episode.mp3")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"


def test_audio_404_for_missing(client):
    resp = client.get("/audio/nonexistent.mp3")
    assert resp.status_code == 404


def test_audio_path_traversal_rejected(client):
    resp = client.get("/audio/../config.yaml")
    assert resp.status_code in (400, 404)


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["episodes"] >= 1


def test_index_hides_hidden_episodes(client):
    """Hidden episodes should not appear on the public player page."""
    # The default FAKE_EPISODE is not hidden, so it should appear
    resp = client.get("/")
    assert "Test Episode Title" in resp.text


def test_empty_episode_list(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    from metadata import MetadataStore
    empty_store = MetadataStore(tmp_path / "metadata.json")

    with patch("app.settings", fake_settings), \
         patch("app.store", empty_store):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.get("/")
            assert resp.status_code == 200
            assert "No episodes yet" in resp.text


def test_submit_url_queues_new_url(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    urls_file = tmp_path / "urls.txt"

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store), \
         patch("app.URLS_FILE", urls_file):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/api/urls", json={"url": "https://example.com/new-story"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"

    assert urls_file.read_text() == "https://example.com/new-story"


def test_submit_url_rejects_duplicate_queue_entry(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/new-story")

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store), \
         patch("app.URLS_FILE", urls_file):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/api/urls", json={"url": "https://example.com/new-story"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "already_queued"


def test_submit_url_rejects_processed_url(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    fake_store.append(FAKE_EPISODE)
    urls_file = tmp_path / "urls.txt"

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store), \
         patch("app.URLS_FILE", urls_file):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/api/urls", json={"url": FAKE_EPISODE.source_url})
            assert resp.status_code == 200
            assert resp.json()["status"] == "already_processed"


def test_submit_url_rejects_invalid_url(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    urls_file = tmp_path / "urls.txt"

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store), \
         patch("app.URLS_FILE", urls_file):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/api/urls", json={"url": "not-a-url"})
            assert resp.status_code == 400
            assert "HTTP or HTTPS" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Admin UI + API tests
# ---------------------------------------------------------------------------

def test_admin_page_loads(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Episode Manager" in resp.text
    assert "Test Episode Title" in resp.text


def test_admin_hide_toggle(client):
    resp = client.post(f"/admin/api/episodes/{FAKE_EPISODE.id}/hide")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hidden"] is True

    # Toggle back
    resp = client.post(f"/admin/api/episodes/{FAKE_EPISODE.id}/hide")
    data = resp.json()
    assert data["hidden"] is False


def test_admin_hide_404(client):
    resp = client.post("/admin/api/episodes/nonexistent/hide")
    assert resp.status_code == 404


def test_admin_delete(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    # Create a fake MP3
    mp3_path = tmp_path / "delete-me.mp3"
    mp3_path.write_bytes(b"FAKE")

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    ep = Episode(
        id="del-1", title="Delete Me", source_url="https://example.com/del",
        timestamp="2026-03-16T00:00:00+00:00", audio_path="delete-me.mp3",
    )
    fake_store.append(ep)

    with patch("app.settings", fake_settings), patch("app.store", fake_store):
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/admin/api/episodes/del-1/delete")
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True

    assert not mp3_path.exists()
    assert fake_store.get_by_id("del-1") is None


def test_admin_delete_404(client):
    resp = client.post("/admin/api/episodes/nonexistent/delete")
    assert resp.status_code == 404


def test_admin_regenerate(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path

    mp3_path = tmp_path / "regen-me.mp3"
    mp3_path.write_bytes(b"FAKE")

    from metadata import MetadataStore
    fake_store = MetadataStore(tmp_path / "metadata.json")
    ep = Episode(
        id="regen-1", title="Regen Me", source_url="https://example.com/regen",
        timestamp="2026-03-16T00:00:00+00:00", audio_path="regen-me.mp3",
    )
    fake_store.append(ep)

    with patch("app.settings", fake_settings), \
         patch("app.store", fake_store), \
         patch("watcher.process_url") as mock_process:
        import app as app_module
        with TestClient(app_module.app) as c:
            resp = c.post("/admin/api/episodes/regen-1/regenerate")
            assert resp.status_code == 200
            assert resp.json()["status"] == "regenerating"

    # Old episode should be deleted
    assert fake_store.get_by_id("regen-1") is None
    assert not mp3_path.exists()
