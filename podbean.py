"""Podbean API client — OAuth auth, file upload, and episode creation."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 3 * 1024 * 1024 * 1024  # 3 GB
AUTH_TIMEOUT = 60
UPLOAD_TIMEOUT = 300
API_TIMEOUT = 60
PODBEAN_TOKEN_URL = "https://api.podbean.com/v1/oauth/token"
PODBEAN_UPLOAD_AUTH_URL = "https://api.podbean.com/v1/files/uploadAuthorize"
PODBEAN_EPISODES_URL = "https://api.podbean.com/v1/episodes"


class PodbeanError(Exception):
    """Raised when any Podbean API call fails."""


def get_access_token(client_id: str, client_secret: str) -> str:
    """Exchange client credentials for an OAuth 2.0 access token."""
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        resp = httpx.post(
            PODBEAN_TOKEN_URL,
            headers={"Authorization": f"Basic {credentials}"},
            data={"grant_type": "client_credentials"},
            timeout=AUTH_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise PodbeanError("Podbean auth request timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error during Podbean auth: {exc}") from exc

    if resp.status_code != 200:
        raise PodbeanError(f"Podbean auth failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise PodbeanError(f"No access_token in Podbean response: {data}")
    return token


def upload_audio(access_token: str, mp3_path: Path) -> str:
    """Upload an MP3 file to Podbean via presigned URL. Returns the file_key."""
    if not mp3_path.exists():
        raise PodbeanError(f"Audio file not found: {mp3_path}")

    file_size = mp3_path.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise PodbeanError(
            f"File too large ({file_size / 1024 / 1024:.0f} MB). Podbean limit is 3 GB."
        )

    # Step 1: Get presigned upload URL
    try:
        resp = httpx.get(
            PODBEAN_UPLOAD_AUTH_URL,
            params={
                "access_token": access_token,
                "filename": mp3_path.name,
                "filesize": file_size,
                "content_type": "audio/mpeg",
            },
            timeout=API_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise PodbeanError("Podbean upload authorization timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error during upload auth: {exc}") from exc

    if resp.status_code != 200:
        raise PodbeanError(f"Upload authorization failed (HTTP {resp.status_code}): {resp.text}")

    auth_data = resp.json()
    presigned_url = auth_data.get("presigned_url")
    file_key = auth_data.get("file_key")
    if not presigned_url or not file_key:
        raise PodbeanError(f"Missing presigned_url or file_key in response: {auth_data}")

    # Step 2: PUT the file to the presigned URL
    logger.info("Uploading %s (%d bytes) to Podbean...", mp3_path.name, file_size)
    try:
        with open(mp3_path, "rb") as f:
            put_resp = httpx.put(
                presigned_url,
                content=f,
                headers={"Content-Type": "audio/mpeg"},
                timeout=UPLOAD_TIMEOUT,
            )
    except httpx.TimeoutException as exc:
        raise PodbeanError("File upload to Podbean timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error during file upload: {exc}") from exc

    if put_resp.status_code >= 400:
        raise PodbeanError(f"File upload failed (HTTP {put_resp.status_code}): {put_resp.text}")

    logger.info("Upload complete, file_key: %s", file_key)
    return file_key


def upload_logo(access_token: str, image_path: Path) -> str:
    """Upload an episode logo image to Podbean. Returns the logo_key."""
    if not image_path.exists():
        raise PodbeanError(f"Image file not found: {image_path}")

    file_size = image_path.stat().st_size
    max_logo_size = 2 * 1024 * 1024  # 2 MB
    if file_size > max_logo_size:
        raise PodbeanError(
            f"Image too large ({file_size / 1024:.0f} KB). Podbean limit is 2 MB."
        )

    suffix = image_path.suffix.lower()
    content_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}
    content_type = content_types.get(suffix, "image/jpeg")

    try:
        resp = httpx.get(
            PODBEAN_UPLOAD_AUTH_URL,
            params={
                "access_token": access_token,
                "filename": image_path.name,
                "filesize": file_size,
                "content_type": content_type,
            },
            timeout=API_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise PodbeanError("Podbean logo upload authorization timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error during logo upload auth: {exc}") from exc

    if resp.status_code != 200:
        raise PodbeanError(f"Logo upload authorization failed (HTTP {resp.status_code}): {resp.text}")

    auth_data = resp.json()
    presigned_url = auth_data.get("presigned_url")
    file_key = auth_data.get("file_key")
    if not presigned_url or not file_key:
        raise PodbeanError(f"Missing presigned_url or file_key in logo response: {auth_data}")

    logger.info("Uploading logo %s (%d bytes) to Podbean...", image_path.name, file_size)
    try:
        with open(image_path, "rb") as f:
            put_resp = httpx.put(
                presigned_url,
                content=f,
                headers={"Content-Type": content_type},
                timeout=API_TIMEOUT,
            )
    except httpx.TimeoutException as exc:
        raise PodbeanError("Logo upload to Podbean timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error during logo upload: {exc}") from exc

    if put_resp.status_code >= 400:
        raise PodbeanError(f"Logo upload failed (HTTP {put_resp.status_code}): {put_resp.text}")

    logger.info("Logo upload complete, file_key: %s", file_key)
    return file_key


def create_episode(
    access_token: str, title: str, description: str, media_key: str,
    status: str = "publish", logo_key: str = "",
) -> tuple[str, str]:
    """Create an episode on Podbean. Returns (episode_id, episode_url)."""
    payload = {
        "access_token": access_token,
        "title": title,
        "content": description,
        "media_key": media_key,
        "type": "public",
        "status": status,
    }
    if logo_key:
        payload["logo_key"] = logo_key

    try:
        resp = httpx.post(
            PODBEAN_EPISODES_URL,
            data=payload,
            timeout=API_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise PodbeanError("Podbean episode creation timed out") from exc
    except httpx.RequestError as exc:
        raise PodbeanError(f"Network error creating episode: {exc}") from exc

    if resp.status_code != 200:
        raise PodbeanError(f"Episode creation failed (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    episode = data.get("episode", {})
    episode_id = episode.get("id", "")
    episode_url = episode.get("permalink_url", "")
    if not episode_id:
        raise PodbeanError(f"No episode ID in Podbean response: {data}")
    return episode_id, episode_url


def publish_episode(
    client_id: str, client_secret: str, mp3_path: Path,
    title: str, description: str, logo_path: Path | None = None,
) -> tuple[str, str]:
    """High-level: auth + upload + create. Returns (episode_id, episode_url)."""
    logger.info("Publishing to Podbean: %s", title)

    token = get_access_token(client_id, client_secret)
    file_key = upload_audio(token, mp3_path)

    logo_key = ""
    if logo_path:
        logo_key = upload_logo(token, logo_path)

    episode_id, episode_url = create_episode(token, title, description, file_key, logo_key=logo_key)

    logger.info("Published to Podbean: %s (%s)", episode_id, episode_url)
    return episode_id, episode_url
