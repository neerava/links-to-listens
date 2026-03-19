"""Unit tests for podbean.py."""
import pytest
import respx
import httpx
from pathlib import Path

from podbean import (
    PodbeanError,
    get_access_token,
    upload_audio,
    create_episode,
    publish_episode,
    PODBEAN_TOKEN_URL,
    PODBEAN_UPLOAD_AUTH_URL,
    PODBEAN_EPISODES_URL,
)


# ---------------------------------------------------------------------------
# get_access_token
# ---------------------------------------------------------------------------

@respx.mock
def test_get_access_token_success():
    respx.post(PODBEAN_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok_123"})
    )
    assert get_access_token("client_id", "client_secret") == "tok_123"


@respx.mock
def test_get_access_token_invalid_credentials():
    respx.post(PODBEAN_TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    with pytest.raises(PodbeanError, match="auth failed"):
        get_access_token("bad_id", "bad_secret")


@respx.mock
def test_get_access_token_network_error():
    respx.post(PODBEAN_TOKEN_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(PodbeanError, match="Network error"):
        get_access_token("id", "secret")


@respx.mock
def test_get_access_token_missing_token():
    respx.post(PODBEAN_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"expires_in": 3600})
    )
    with pytest.raises(PodbeanError, match="No access_token"):
        get_access_token("id", "secret")


# ---------------------------------------------------------------------------
# upload_audio
# ---------------------------------------------------------------------------

@respx.mock
def test_upload_audio_success(tmp_path):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" * 100)

    respx.get(PODBEAN_UPLOAD_AUTH_URL).mock(
        return_value=httpx.Response(200, json={
            "presigned_url": "https://s3.example.com/upload",
            "file_key": "fk_abc",
        })
    )
    respx.put("https://s3.example.com/upload").mock(
        return_value=httpx.Response(200)
    )

    assert upload_audio("tok_123", mp3) == "fk_abc"


def test_upload_audio_file_not_found(tmp_path):
    missing = tmp_path / "nope.mp3"
    with pytest.raises(PodbeanError, match="not found"):
        upload_audio("tok_123", missing)


def test_upload_audio_file_too_large(tmp_path, monkeypatch):
    mp3 = tmp_path / "huge.mp3"
    mp3.write_bytes(b"\x00")

    import os

    class FakeStat:
        st_size = 4 * 1024 * 1024 * 1024  # 4 GB

    monkeypatch.setattr(Path, "stat", lambda self: FakeStat())
    with pytest.raises(PodbeanError, match="too large"):
        upload_audio("tok_123", mp3)


@respx.mock
def test_upload_audio_auth_failure(tmp_path):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00")

    respx.get(PODBEAN_UPLOAD_AUTH_URL).mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    with pytest.raises(PodbeanError, match="authorization failed"):
        upload_audio("tok_123", mp3)


# ---------------------------------------------------------------------------
# create_episode
# ---------------------------------------------------------------------------

@respx.mock
def test_create_episode_success():
    respx.post(PODBEAN_EPISODES_URL).mock(
        return_value=httpx.Response(200, json={
            "episode": {"id": "ep_123", "permalink_url": "https://podbean.com/ep/ep_123"}
        })
    )
    eid, eurl = create_episode("tok_123", "Title", "Desc", "fk_abc")
    assert eid == "ep_123"
    assert eurl == "https://podbean.com/ep/ep_123"


@respx.mock
def test_create_episode_api_error():
    respx.post(PODBEAN_EPISODES_URL).mock(
        return_value=httpx.Response(400, text="Bad request")
    )
    with pytest.raises(PodbeanError, match="creation failed"):
        create_episode("tok_123", "Title", "Desc", "fk_abc")


@respx.mock
def test_create_episode_missing_id():
    respx.post(PODBEAN_EPISODES_URL).mock(
        return_value=httpx.Response(200, json={"episode": {}})
    )
    with pytest.raises(PodbeanError, match="No episode ID"):
        create_episode("tok_123", "Title", "Desc", "fk_abc")


# ---------------------------------------------------------------------------
# publish_episode (end-to-end)
# ---------------------------------------------------------------------------

@respx.mock
def test_publish_episode_full_flow(tmp_path):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" * 100)

    respx.post(PODBEAN_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok_123"})
    )
    respx.get(PODBEAN_UPLOAD_AUTH_URL).mock(
        return_value=httpx.Response(200, json={
            "presigned_url": "https://s3.example.com/upload",
            "file_key": "fk_abc",
        })
    )
    respx.put("https://s3.example.com/upload").mock(
        return_value=httpx.Response(200)
    )
    respx.post(PODBEAN_EPISODES_URL).mock(
        return_value=httpx.Response(200, json={
            "episode": {"id": "ep_999", "permalink_url": "https://podbean.com/ep/ep_999"}
        })
    )

    eid, eurl = publish_episode("cid", "csec", mp3, "My Episode", "A description")
    assert eid == "ep_999"
    assert eurl == "https://podbean.com/ep/ep_999"


@respx.mock
def test_publish_episode_auth_failure_stops_early(tmp_path):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00")

    respx.post(PODBEAN_TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    # upload and create should never be called
    upload_route = respx.get(PODBEAN_UPLOAD_AUTH_URL)
    create_route = respx.post(PODBEAN_EPISODES_URL)

    with pytest.raises(PodbeanError, match="auth failed"):
        publish_episode("bad", "bad", mp3, "Title", "Desc")

    assert not upload_route.called
    assert not create_route.called
