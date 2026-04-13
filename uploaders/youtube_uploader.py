"""
YouTube Shorts uploader — uploads a video as a YouTube Short using the
YouTube Data API v3 with OAuth2 authentication.

Setup:
  1. Go to https://console.cloud.google.com
  2. Create a project, enable "YouTube Data API v3"
  3. Create OAuth2 credentials (Desktop app), download as client_secrets.json
  4. Set YOUTUBE_OAUTH_CREDENTIALS_FILE=client_secrets.json in your .env
  5. On first run you will be prompted to authorise in your browser; a token
     file is saved for subsequent runs.
"""

import logging
import os
import pickle
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from utils.helpers import require_env, optional_env

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PICKLE = "youtube_token.pickle"


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, "rb") as f:
            creds = pickle.load(f)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        client_secrets = optional_env(
            "YOUTUBE_OAUTH_CREDENTIALS_FILE", "client_secrets.json"
        )
        if not os.path.exists(client_secrets):
            raise FileNotFoundError(
                f"YouTube OAuth credentials file not found: {client_secrets}. "
                "Download it from Google Cloud Console and set "
                "YOUTUBE_OAUTH_CREDENTIALS_FILE in your .env"
            )
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
        creds = flow.run_local_server(port=0)

    with open(TOKEN_PICKLE, "wb") as f:
        pickle.dump(creds, f)

    return creds


def upload_short(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    privacy: str = "public",
) -> str:
    """
    Upload a video as a YouTube Short.

    Returns the YouTube video ID of the uploaded video.
    Raises RuntimeError on failure.

    Args:
        video_path:  Path to the processed MP4 file
        title:       Video title (will have #Shorts appended if not present)
        description: Video description
        tags:        List of tags
        privacy:     "public" | "unlisted" | "private"
    """
    if "#Shorts" not in title and "#shorts" not in title:
        title = f"{title} #Shorts"

    if "#Shorts" not in description:
        description = f"{description}\n\n#Shorts".strip()

    tags = tags or []

    try:
        creds = _get_credentials()
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:500],
                "categoryId": "20",  # Gaming — change to "22" for People & Blogs
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 5,  # 5 MB chunks
        )

        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.debug("Upload progress: %.0f%%", status.progress() * 100)

        video_id = response["id"]
        logger.info(
            "Uploaded to YouTube Shorts: https://www.youtube.com/shorts/%s", video_id
        )
        return video_id

    except HttpError as exc:
        raise RuntimeError(f"YouTube upload failed: {exc}") from exc
