"""Unit tests for pipeline_state.py — new load/delete/failed_at_stage features."""
import json
import pytest
from pathlib import Path

from pipeline_state import PipelineRun, PipelineStateStore, Stage


@pytest.fixture()
def store(tmp_path):
    return PipelineStateStore(pipeline_dir=tmp_path, retention_days=3)


def test_load_all_runs_empty(store):
    assert store.load_all_runs() == []


def test_load_all_runs_sorted(store):
    r1 = store.create("https://example.com/1")
    r2 = store.create("https://example.com/2")
    r3 = store.create("https://example.com/3")
    runs = store.load_all_runs()
    assert len(runs) == 3
    # Most recent first
    assert runs[0].url == "https://example.com/3"
    assert runs[2].url == "https://example.com/1"


def test_load_run_found(store):
    r = store.create("https://example.com/test")
    loaded = store.load_run(r.id)
    assert loaded is not None
    assert loaded.url == "https://example.com/test"
    assert loaded.stage == Stage.PENDING


def test_load_run_not_found(store):
    assert store.load_run("nonexistent") is None


def test_delete_run(store):
    r = store.create("https://example.com/delete-me")
    run_dir = Path(r.run_dir)
    assert run_dir.exists()
    assert store.delete_run(r.id) is True
    assert not run_dir.exists()
    assert store.load_run(r.id) is None


def test_delete_run_not_found(store):
    assert store.delete_run("nonexistent") is False


def test_failed_at_stage_recorded_from_script(store):
    r = store.create("https://example.com/fail")
    store.transition(r, Stage.SCRIPT)
    store.transition(r, Stage.FAILED, error="scrape error")
    loaded = store.load_run(r.id)
    assert loaded.stage == Stage.FAILED
    assert loaded.failed_at_stage == "script"
    assert loaded.error == "scrape error"


def test_failed_at_stage_recorded_from_tts(store):
    r = store.create("https://example.com/fail-tts")
    store.transition(r, Stage.SCRIPT)
    store.transition(r, Stage.TTS)
    store.transition(r, Stage.FAILED, error="tts error")
    loaded = store.load_run(r.id)
    assert loaded.failed_at_stage == "tts"


def test_metadata_fields_saved(store):
    r = store.create("https://example.com/meta")
    store.transition(r, Stage.SCRIPT,
                     title="My Title",
                     description="My Desc",
                     thumbnail_url="https://example.com/img.jpg")
    loaded = store.load_run(r.id)
    assert loaded.title == "My Title"
    assert loaded.description == "My Desc"
    assert loaded.thumbnail_url == "https://example.com/img.jpg"


def test_from_dict_backward_compat():
    """Old state.json files without new fields should load without error."""
    old_data = {
        "id": "test-id",
        "url": "https://example.com",
        "stage": "done",
        "created_at": "2026-03-20T00:00:00+00:00",
        "updated_at": "2026-03-20T00:01:00+00:00",
    }
    run = PipelineRun.from_dict(old_data)
    assert run.failed_at_stage == ""
    assert run.title == ""
    assert run.description == ""
    assert run.thumbnail_url == ""
