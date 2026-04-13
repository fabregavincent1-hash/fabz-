"""
Twitch clip source — fetches top clips for a list of streamers via the
Twitch Helix API using client-credentials authentication.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from utils.helpers import require_env

logger = logging.getLogger(__name__)

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API_BASE = "https://api.twitch.tv/helix"

_token_cache: dict = {}


@dataclass
class TwitchClip:
    clip_id: str
    url: str
    embed_url: str
    broadcaster_name: str
    title: str
    view_count: int
    duration: float
    thumbnail_url: str
    created_at: str


def _get_access_token(client_id: str, client_secret: str) -> str:
    """Obtain (or return cached) a client-credentials OAuth token."""
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["token"]

    resp = requests.post(
        TWITCH_TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


def _get_headers(client_id: str, token: str) -> dict:
    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
    }


def _get_broadcaster_id(
    client_id: str, token: str, login: str
) -> Optional[str]:
    resp = requests.get(
        f"{TWITCH_API_BASE}/users",
        headers=_get_headers(client_id, token),
        params={"login": login},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        logger.warning("Twitch user not found: %s", login)
        return None
    return data[0]["id"]


def get_top_clips(
    streamer_login: str,
    limit: int = 5,
    period: str = "week",
    min_views: int = 0,
    max_duration: float = 60.0,
) -> list[TwitchClip]:
    """
    Return the top clips for a Twitch streamer sorted by view count.

    Args:
        streamer_login: Twitch username (e.g. "shroud")
        limit:          Max number of clips to return
        period:         "day" | "week" | "month" | "all"
        min_views:      Skip clips below this view count
        max_duration:   Skip clips longer than this (seconds)
    """
    client_id = require_env("TWITCH_CLIENT_ID")
    client_secret = require_env("TWITCH_CLIENT_SECRET")

    try:
        token = _get_access_token(client_id, client_secret)
        broadcaster_id = _get_broadcaster_id(client_id, token, streamer_login)
        if not broadcaster_id:
            return []

        # Map period strings to Twitch started_at param
        # Twitch doesn't have a "period" param for clips — we fetch by created_at window
        from datetime import datetime, timedelta, timezone

        period_map = {
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
            "all": None,
        }
        delta = period_map.get(period, timedelta(weeks=1))

        params: dict = {
            "broadcaster_id": broadcaster_id,
            "first": min(100, max(limit * 4, 20)),  # over-fetch then filter
        }
        if delta:
            started_at = (datetime.now(timezone.utc) - delta).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            params["started_at"] = started_at

        resp = requests.get(
            f"{TWITCH_API_BASE}/clips",
            headers=_get_headers(client_id, token),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        raw_clips = resp.json().get("data", [])

    except requests.RequestException as exc:
        logger.error("Twitch API error for %s: %s", streamer_login, exc)
        return []

    clips: list[TwitchClip] = []
    for c in raw_clips:
        view_count = c.get("view_count", 0)
        duration = float(c.get("duration", 0))
        if view_count < min_views:
            continue
        if duration > max_duration:
            continue
        clips.append(
            TwitchClip(
                clip_id=c["id"],
                url=c["url"],
                embed_url=c.get("embed_url", ""),
                broadcaster_name=c.get("broadcaster_name", streamer_login),
                title=c.get("title", ""),
                view_count=view_count,
                duration=duration,
                thumbnail_url=c.get("thumbnail_url", ""),
                created_at=c.get("created_at", ""),
            )
        )

    # Sort by view count descending, take top N
    clips.sort(key=lambda x: x.view_count, reverse=True)
    result = clips[:limit]
    logger.info(
        "Twitch [%s]: fetched %d clips (from %d candidates)",
        streamer_login,
        len(result),
        len(raw_clips),
    )
    return result
