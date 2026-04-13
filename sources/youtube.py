"""
YouTube clip source — fetches short-form clips / highlight videos from a
YouTube channel using the YouTube Data API v3.

Strategy:
  1. Use the YouTube Clips endpoint (if available for the channel).
  2. Fall back to searching for recent short videos (≤60 s) from the channel.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils.helpers import require_env

logger = logging.getLogger(__name__)

YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"


@dataclass
class YouTubeClip:
    clip_id: str       # YouTube video ID
    url: str
    channel_id: str
    channel_title: str
    title: str
    view_count: int
    duration_seconds: float
    published_at: str


def _build_client():
    api_key = require_env("YOUTUBE_API_KEY")
    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, developerKey=api_key)


def _iso_duration_to_seconds(duration: str) -> float:
    """Parse ISO 8601 duration string (e.g. PT1M30S) into total seconds."""
    import re
    pattern = re.compile(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", re.IGNORECASE
    )
    match = pattern.match(duration)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def get_clips(
    channel_id: str,
    limit: int = 5,
    period: str = "week",
    min_views: int = 0,
    max_duration: float = 60.0,
) -> list[YouTubeClip]:
    """
    Return top clips (short videos) from a YouTube channel.

    Args:
        channel_id:   YouTube channel ID (e.g. "UCxxxxxx")
        limit:        Max clips to return
        period:       "day" | "week" | "month" | "all"
        min_views:    Skip videos with fewer views
        max_duration: Skip videos longer than this (seconds)
    """
    try:
        youtube = _build_client()
    except Exception as exc:
        logger.error("Failed to build YouTube client: %s", exc)
        return []

    period_map = {
        "day": timedelta(days=1),
        "week": timedelta(weeks=1),
        "month": timedelta(days=30),
        "all": None,
    }
    delta = period_map.get(period, timedelta(weeks=1))
    published_after: str | None = None
    if delta:
        dt = datetime.now(timezone.utc) - delta
        published_after = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # Search for recent videos from the channel — over-fetch then filter
        search_params: dict = {
            "part": "id,snippet",
            "channelId": channel_id,
            "type": "video",
            "videoDuration": "short",   # YouTube "short" = under 4 min; we filter further
            "order": "viewCount",
            "maxResults": min(50, limit * 8),
        }
        if published_after:
            search_params["publishedAfter"] = published_after

        search_resp = youtube.search().list(**search_params).execute()
        items = search_resp.get("items", [])

        if not items:
            logger.info("YouTube [%s]: no videos found", channel_id)
            return []

        video_ids = [item["id"]["videoId"] for item in items]

        # Fetch detailed stats (duration, view count)
        details_resp = (
            youtube.videos()
            .list(
                part="snippet,contentDetails,statistics",
                id=",".join(video_ids),
            )
            .execute()
        )

    except HttpError as exc:
        logger.error("YouTube API error for channel %s: %s", channel_id, exc)
        return []

    clips: list[YouTubeClip] = []
    for video in details_resp.get("items", []):
        vid_id = video["id"]
        snippet = video.get("snippet", {})
        stats = video.get("statistics", {})
        content = video.get("contentDetails", {})

        duration_secs = _iso_duration_to_seconds(content.get("duration", "PT0S"))
        view_count = int(stats.get("viewCount", 0))

        if view_count < min_views:
            continue
        if duration_secs > max_duration:
            continue

        clips.append(
            YouTubeClip(
                clip_id=vid_id,
                url=f"https://www.youtube.com/watch?v={vid_id}",
                channel_id=snippet.get("channelId", channel_id),
                channel_title=snippet.get("channelTitle", ""),
                title=snippet.get("title", ""),
                view_count=view_count,
                duration_seconds=duration_secs,
                published_at=snippet.get("publishedAt", ""),
            )
        )

    clips.sort(key=lambda x: x.view_count, reverse=True)
    result = clips[:limit]
    logger.info(
        "YouTube [%s]: fetched %d clips (from %d candidates)",
        channel_id,
        len(result),
        len(video_ids),
    )
    return result
