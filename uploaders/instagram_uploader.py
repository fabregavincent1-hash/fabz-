"""
Instagram Reels uploader — posts a video as an Instagram Reel using the
Instagram Graph API.

Setup:
  1. Create a Facebook app at https://developers.facebook.com
  2. Add "Instagram Graph API" product
  3. Connect your Instagram Professional (Business or Creator) account
  4. Generate a long-lived access token with instagram_content_publish scope
  5. Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID in your .env

Note: Your video must be publicly accessible via a URL for the Graph API to
fetch it. This uploader uses a temporary hosting approach: it first uploads
the video to the Graph API using the resumable upload endpoint, then creates
a container and publishes it.
"""

import logging
import os
import time

import requests

from utils.helpers import require_env

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def _get_credentials() -> tuple[str, str]:
    token = require_env("INSTAGRAM_ACCESS_TOKEN")
    account_id = require_env("INSTAGRAM_ACCOUNT_ID")
    return token, account_id


def _upload_video_to_graph(
    video_path: str, token: str, account_id: str
) -> str:
    """
    Upload video bytes to Instagram using the resumable upload session API.
    Returns the video_handle needed for the media container.
    """
    file_size = os.path.getsize(video_path)

    # Start upload session
    session_resp = requests.post(
        f"{GRAPH_API_BASE}/{account_id}/video_reels",
        params={
            "upload_phase": "start",
            "access_token": token,
        },
        timeout=30,
    )
    if session_resp.status_code != 200:
        raise RuntimeError(
            f"Instagram upload session failed [{session_resp.status_code}]: "
            f"{session_resp.text}"
        )

    session_data = session_resp.json()
    video_id = session_data.get("video_id")
    upload_url = session_data.get("upload_url")

    if not video_id or not upload_url:
        raise RuntimeError(
            f"Missing video_id/upload_url in session response: {session_resp.text}"
        )

    # Transfer video bytes
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    transfer_resp = requests.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(file_size),
        },
        data=video_bytes,
        timeout=300,
    )
    if transfer_resp.status_code != 200:
        raise RuntimeError(
            f"Instagram video transfer failed [{transfer_resp.status_code}]: "
            f"{transfer_resp.text}"
        )

    return video_id


def _create_media_container(
    video_id: str,
    caption: str,
    token: str,
    account_id: str,
) -> str:
    """Create an Instagram media container for the Reel. Returns container_id."""
    resp = requests.post(
        f"{GRAPH_API_BASE}/{account_id}/media",
        params={
            "media_type": "REELS",
            "video_id": video_id,
            "caption": caption[:2200],
            "share_to_feed": "true",
            "access_token": token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Instagram container creation failed [{resp.status_code}]: {resp.text}"
        )
    return resp.json()["id"]


def _wait_for_container(container_id: str, token: str, max_wait: int = 120) -> None:
    """Poll until the media container is ready to publish."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{container_id}",
            params={
                "fields": "status_code",
                "access_token": token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram container processing failed: {resp.text}")
        time.sleep(5)
    raise TimeoutError("Instagram container did not finish processing in time")


def _publish_container(container_id: str, token: str, account_id: str) -> str:
    """Publish a ready container. Returns the published media ID."""
    resp = requests.post(
        f"{GRAPH_API_BASE}/{account_id}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Instagram publish failed [{resp.status_code}]: {resp.text}"
        )
    return resp.json()["id"]


def upload_reel(
    video_path: str,
    caption: str,
    hashtags: list[str] | None = None,
) -> str:
    """
    Upload a video as an Instagram Reel.

    Returns the Instagram media ID of the published Reel.
    Raises RuntimeError on failure.

    Args:
        video_path: Path to the processed MP4 file
        caption:    Caption text
        hashtags:   List of hashtag strings without # prefix
    """
    token, account_id = _get_credentials()

    if hashtags:
        tag_str = " ".join(f"#{h}" for h in hashtags)
        full_caption = f"{caption}\n\n{tag_str}".strip()
    else:
        full_caption = caption

    logger.info("Uploading Reel to Instagram...")

    video_id = _upload_video_to_graph(video_path, token, account_id)
    container_id = _create_media_container(video_id, full_caption, token, account_id)
    _wait_for_container(container_id, token)
    media_id = _publish_container(container_id, token, account_id)

    logger.info("Published Instagram Reel: media_id=%s", media_id)
    return media_id
