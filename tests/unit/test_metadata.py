"""Unit tests for metadata.py."""
import json
import threading
from pathlib import Path

import pytest

from metadata import MetadataStore
from models import Episode


def _ep(n: int = 1) -> Episode:
    return Episode(
        id=f"id-{n}",
        title=f"Episode {n}",
        description=f"Description for episode {n}.",
        source_url=f"https://example.com/{n}",
        timestamp="2026-03-15T10:00:00+00:00",
        audio_path=f"episode-{n}.mp3",
        thumbnail_url=f"https://example.com/img/{n}.jpg",
    )


def test_load_empty_when_no_file(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    assert store.load() == []


def test_append_and_load(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    ep = _ep(1)
    store.append(ep)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == ep.id
    assert loaded[0].source_url == ep.source_url


def test_multiple_appends(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    for i in range(5):
        store.append(_ep(i))
    assert len(store.load()) == 5


def test_is_processed_true(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    store.append(_ep(1))
    assert store.is_processed("https://example.com/1") is True


def test_is_processed_false(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    assert store.is_processed("https://example.com/99") is False


def test_persists_across_instances(tmp_path):
    path = tmp_path / "metadata.json"
    store1 = MetadataStore(path)
    store1.append(_ep(1))

    store2 = MetadataStore(path)
    assert len(store2.load()) == 1


def test_atomic_write_leaves_no_tmp_on_success(tmp_path):
    path = tmp_path / "metadata.json"
    store = MetadataStore(path)
    store.append(_ep(1))
    assert not (tmp_path / "metadata.json.tmp").exists()


def test_load_handles_corrupt_file(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text("NOT VALID JSON")
    store = MetadataStore(path)
    assert store.load() == []


def test_load_handles_wrong_type(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"not": "a list"}))
    store = MetadataStore(path)
    assert store.load() == []


def test_concurrent_appends(tmp_path):
    path = tmp_path / "metadata.json"
    store = MetadataStore(path)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            store.append(_ep(n))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(store.load()) == 10


def test_episode_roundtrip():
    ep = _ep(42)
    restored = Episode.from_dict(ep.to_dict())
    assert restored.id == ep.id
    assert restored.title == ep.title
    assert restored.description == ep.description
    assert restored.source_url == ep.source_url
    assert restored.timestamp == ep.timestamp
    assert restored.audio_path == ep.audio_path
    assert restored.thumbnail_url == ep.thumbnail_url


def test_episode_from_dict_without_description():
    """Old metadata without description field should load with empty string."""
    data = {
        "id": "old-id",
        "title": "Old Episode",
        "source_url": "https://example.com/old",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "audio_path": "old.mp3",
    }
    ep = Episode.from_dict(data)
    assert ep.description == ""


# ---------------------------------------------------------------------------
# MetadataStore: get_by_id, update, delete
# ---------------------------------------------------------------------------

def test_get_by_id_found(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    store.append(_ep(1))
    assert store.get_by_id("id-1") is not None
    assert store.get_by_id("id-1").title == "Episode 1"


def test_get_by_id_not_found(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    assert store.get_by_id("nonexistent") is None


def test_update_episode(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    ep = _ep(1)
    store.append(ep)
    ep.title = "Updated Title"
    ep.hidden = True
    assert store.update(ep) is True
    loaded = store.get_by_id("id-1")
    assert loaded.title == "Updated Title"
    assert loaded.hidden is True


def test_update_nonexistent_returns_false(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    ep = _ep(99)
    assert store.update(ep) is False


def test_delete_episode(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    store.append(_ep(1))
    store.append(_ep(2))
    removed = store.delete("id-1")
    assert removed is not None
    assert removed.id == "id-1"
    assert len(store.load()) == 1
    assert store.load()[0].id == "id-2"


def test_delete_nonexistent_returns_none(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    assert store.delete("nonexistent") is None


def test_hidden_field_roundtrip(tmp_path):
    store = MetadataStore(tmp_path / "metadata.json")
    ep = _ep(1)
    ep.hidden = True
    store.append(ep)
    loaded = store.load()
    assert loaded[0].hidden is True


def test_hidden_defaults_false():
    ep = Episode.from_dict({
        "id": "x", "title": "T", "source_url": "u",
        "timestamp": "t", "audio_path": "a",
    })
    assert ep.hidden is False
