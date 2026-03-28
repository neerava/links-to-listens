"""Integration tests for the pipeline API."""
import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pipeline_state import PipelineStateStore, Stage


@pytest.fixture()
def client(tmp_path):
    from config import Settings
    fake_settings = Settings()
    fake_settings.output_path = tmp_path
    fake_settings.pipeline_path = tmp_path / "pipeline"

    store = PipelineStateStore(
        pipeline_dir=fake_settings.pipeline_path,
        retention_days=3,
    )

    from metadata import MetadataStore
    fake_meta = MetadataStore(tmp_path / "metadata.json")

    with patch("pipeline_api._settings", fake_settings), \
         patch("pipeline_api._pipeline_store", store), \
         patch("pipeline_api._metadata_store", fake_meta), \
         patch("app.settings", fake_settings), \
         patch("app.store", fake_meta):
        import app as app_module
        with TestClient(app_module.app) as c:
            yield c, store


def test_list_runs_empty(client):
    c, store = client
    resp = c.get("/pipeline/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_runs_returns_runs(client):
    c, store = client
    store.create("https://example.com/1")
    store.create("https://example.com/2")
    resp = c.get("/pipeline/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["url"] == "https://example.com/2"  # most recent first


def test_delete_run(client):
    c, store = client
    r = store.create("https://example.com/delete")
    resp = c.post(f"/pipeline/api/runs/{r.id}/delete")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert store.load_run(r.id) is None


def test_delete_run_not_found(client):
    c, store = client
    resp = c.post("/pipeline/api/runs/nonexistent/delete")
    assert resp.status_code == 404


def test_retry_not_found(client):
    c, store = client
    resp = c.post("/pipeline/api/runs/nonexistent/retry")
    assert resp.status_code == 404


def test_retry_not_failed(client):
    c, store = client
    r = store.create("https://example.com/ok")
    store.transition(r, Stage.DONE)
    resp = c.post(f"/pipeline/api/runs/{r.id}/retry")
    assert resp.status_code == 400
    assert "not in FAILED" in resp.json()["detail"]


def test_retry_failed_run(client):
    c, store = client
    r = store.create("https://example.com/fail")
    store.transition(r, Stage.SCRIPT)
    store.transition(r, Stage.FAILED, error="test error")

    with patch("watcher.resume_pipeline"):
        resp = c.post(f"/pipeline/api/runs/{r.id}/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "retrying"
        assert resp.json()["from_stage"] == "script"


def test_pipeline_page_loads(client):
    c, store = client
    resp = c.get("/pipeline")
    assert resp.status_code == 200
    assert "Pipeline Runs" in resp.text


# ---------------------------------------------------------------------------
# GET single run + file contents
# ---------------------------------------------------------------------------

def test_get_run(client):
    c, store = client
    r = store.create("https://example.com/detail")
    resp = c.get(f"/pipeline/api/runs/{r.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://example.com/detail"
    assert data["input_text_content"] is None  # no file yet


def test_get_run_with_file_content(client):
    c, store = client
    r = store.create("https://example.com/with-content")
    store.save_input_text(r, "Hello scraped text")
    resp = c.get(f"/pipeline/api/runs/{r.id}")
    data = resp.json()
    assert data["input_text_content"] == "Hello scraped text"


def test_get_run_not_found(client):
    c, store = client
    resp = c.get("/pipeline/api/runs/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST restart (any status)
# ---------------------------------------------------------------------------

def test_restart_done_run(client):
    c, store = client
    r = store.create("https://example.com/done")
    store.transition(r, Stage.SCRIPT)
    store.transition(r, Stage.TTS)
    store.transition(r, Stage.DONE)

    with patch("watcher.restart_pipeline"):
        resp = c.post(f"/pipeline/api/runs/{r.id}/restart",
                      json={"from_stage": "script"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarting"


def test_restart_failed_run(client):
    c, store = client
    r = store.create("https://example.com/failed")
    store.transition(r, Stage.SCRIPT)
    store.transition(r, Stage.FAILED, error="oops")

    with patch("watcher.restart_pipeline"):
        resp = c.post(f"/pipeline/api/runs/{r.id}/restart",
                      json={"from_stage": "tts", "script_text": "Custom script"})
        assert resp.status_code == 200
        assert resp.json()["from_stage"] == "tts"


def test_restart_active_run_rejected(client):
    c, store = client
    r = store.create("https://example.com/active")
    store.transition(r, Stage.SCRIPT)
    resp = c.post(f"/pipeline/api/runs/{r.id}/restart",
                  json={"from_stage": "script"})
    assert resp.status_code == 409
    assert "currently active" in resp.json()["detail"]


def test_restart_invalid_stage(client):
    c, store = client
    r = store.create("https://example.com/bad")
    store.transition(r, Stage.DONE)
    resp = c.post(f"/pipeline/api/runs/{r.id}/restart",
                  json={"from_stage": "invalid"})
    assert resp.status_code == 400


def test_pipeline_detail_page_loads(client):
    c, store = client
    r = store.create("https://example.com/detail-page")
    resp = c.get(f"/pipeline/{r.id}")
    assert resp.status_code == 200
    assert "Run Detail" in resp.text
