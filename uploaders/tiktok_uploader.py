"""
TikTok uploader — posts a video to TikTok using the Content Posting API.

Setup:
  1. Apply for TikTok for Developers access at https://developers.tiktok.com
  2. Create an app and request "Content Posting API" scope
  3. Complete OAuth flow to obtain an access token
  4. Set TIKTOK_ACCESS_TOKEN in your .env

Note: TikTok's Content Posting API requires business/creator account approval.
The access token expires; you will need to refresh it via your TikTok app OAuth
flow when it expires (token lifetime varies, typically 24h–30 days).
"""

import logging
import os
import time

import requests

from utils.helpers import require_env

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


def _get_token() -> str:
    return require_env("TIKTOK_ACCESS_TOKEN")


def upload_video(
    video_path: str,
    caption: str,
    hashtags: list[str] | None = None,
) -> str:
    """
    Upload a video to TikTok via the Content Posting API (direct post).

    Returns the TikTok publish_id on success.
    Raises RuntimeError on failure.

    Args:
        video_path: Path to the processed MP4 file
        caption:    Caption text (max 150 chars)
        hashtags:   List of hashtag strings without # prefix
    """
    token = _get_token()
    file_size = os.path.getsize(video_path)

    if hashtags:
        tag_str = " ".join(f"#{h}" for h in hashtags)
        full_caption = f"{caption} {tag_str}".strip()
    else:
        full_caption = caption

    full_caption = full_caption[:2200]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Step 1: Initialize upload
    init_body = {
        "post_info": {
            "title": full_caption[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": min(file_size, 10 * 1024 * 1024),  # up to 10 MB chunks
            "total_chunk_count": max(1, (file_size + 10 * 1024 * 1024 - 1) // (10 * 1024 * 1024)),
        },
    }

    resp = requests.post(
        f"{TIKTOK_API_BASE}/post/publish/video/init/",
        json=init_body,
        headers=headers,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"TikTok init failed [{resp.status_code}]: {resp.text}"
        )

    data = resp.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")

    if not publish_id or not upload_url:
        raise RuntimeError(f"TikTok init missing publish_id/upload_url: {resp.text}")

    # Step 2: Upload video file
    chunk_size = 10 * 1024 * 1024  # 10 MB
    with open(video_path, "rb") as f:
        chunk_index = 0
        offset = 0
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            chunk_end = offset + len(chunk) - 1

            upload_headers = {
                "Content-Range": f"bytes {offset}-{chunk_end}/{file_size}",
                "Content-Type": "video/mp4",
            }
            upload_resp = requests.put(
                upload_url,
                data=chunk,
                headers=upload_headers,
                timeout=120,
            )
            if upload_resp.status_code not in (200, 201, 206):
                raise RuntimeError(
                    f"TikTok chunk upload failed [{upload_resp.status_code}]: "
                    f"{upload_resp.text}"
                )
            offset += len(chunk)
            chunk_index += 1

    logger.info("TikTok upload complete. publish_id=%s", publish_id)
    return publish_id
