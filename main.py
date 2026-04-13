"""
fabz- : Automated Stream Clipping Bot
======================================
Main pipeline — run this directly for a one-shot pass, or use
scheduler.py to run it on a recurring schedule.

Usage:
    python main.py                        # use default config.yaml
    python main.py --config myconfig.yaml # use custom config
    python main.py --dry-run              # fetch & process, skip uploads
"""

import argparse
import logging
import os
import sys
from typing import Any

from db.database import (
    init_db,
    is_clip_known,
    save_clip,
    mark_clip_processed,
    record_upload,
    has_been_uploaded,
)
from processor.caption_generator import add_captions
from processor.clip_processor import process_clip
from sources.twitch import get_top_clips as get_twitch_clips
from sources.youtube import get_clips as get_youtube_clips
from uploaders.instagram_uploader import upload_reel
from uploaders.tiktok_uploader import upload_video as tiktok_upload
from uploaders.youtube_uploader import upload_short
from utils.helpers import ensure_dir, load_config, safe_filename, setup_logging

logger = logging.getLogger(__name__)


def _build_tags(streamer: str, source: str, title: str) -> list[str]:
    base = ["clips", "gaming", "streamer", "highlights", source]
    extra = [w.lower() for w in streamer.split() + title.split() if len(w) > 2]
    return list(dict.fromkeys(base + extra))[:30]


def _process_and_upload(
    cfg: dict[str, Any],
    db_path: str,
    source: str,
    clip_id: str,
    clip_url: str,
    streamer: str,
    title: str,
    view_count: int,
    duration: float,
    dry_run: bool = False,
) -> None:
    """
    Full pipeline for one clip: download → process → caption → upload to all
    configured destinations.
    """
    download_dir: str = cfg["paths"]["downloads"]
    output_dir: str = cfg["paths"]["output"]
    proc_cfg = cfg["processing"]
    upload_cfg = cfg["upload_to"]

    row_id = save_clip(
        db_path,
        source=source,
        clip_id=clip_id,
        clip_url=clip_url,
        streamer=streamer,
        title=title,
        view_count=view_count,
        duration=duration,
    )

    # Process video
    try:
        logger.info("Processing clip: %s — %s", streamer, title)
        processed_path = process_clip(
            url=clip_url,
            download_dir=download_dir,
            output_dir=output_dir,
            title=title or clip_id,
            channel_name=proc_cfg["branding"]["channel_name"],
            watermark_position=proc_cfg["branding"]["watermark_position"],
            logo_path=proc_cfg["branding"].get("logo_path", ""),
            vertical=proc_cfg.get("vertical_format", True),
        )
    except Exception as exc:
        logger.error("Processing failed for %s (%s): %s", clip_id, source, exc)
        return

    # Optional: add captions
    if proc_cfg.get("add_captions", False):
        captioned_path = processed_path.replace("_final.mp4", "_captioned.mp4")
        try:
            add_captions(processed_path, captioned_path)
            os.remove(processed_path)
            processed_path = captioned_path
        except Exception as exc:
            logger.warning("Caption generation failed (skipping): %s", exc)

    mark_clip_processed(db_path, row_id)

    if dry_run:
        logger.info("[dry-run] Skipping uploads for %s", processed_path)
        return

    tags = _build_tags(streamer, source, title)
    caption = f"{title} | {streamer} #{source.capitalize()} #clips"

    # YouTube Shorts
    if upload_cfg.get("youtube_shorts") and not has_been_uploaded(
        db_path, row_id, "youtube_shorts"
    ):
        try:
            yt_id = upload_short(
                processed_path,
                title=f"{title} — {streamer}",
                description=f"Top clip by {streamer}\n\n#Shorts #clips #{source}",
                tags=tags,
            )
            record_upload(db_path, row_id, "youtube_shorts", post_id=yt_id)
        except Exception as exc:
            logger.error("YouTube Shorts upload failed: %s", exc)
            record_upload(db_path, row_id, "youtube_shorts", status="failed", error=str(exc))

    # TikTok
    if upload_cfg.get("tiktok") and not has_been_uploaded(db_path, row_id, "tiktok"):
        try:
            tt_id = tiktok_upload(
                processed_path,
                caption=caption,
                hashtags=["clips", "gaming", source, streamer.replace(" ", "")],
            )
            record_upload(db_path, row_id, "tiktok", post_id=tt_id)
        except Exception as exc:
            logger.error("TikTok upload failed: %s", exc)
            record_upload(db_path, row_id, "tiktok", status="failed", error=str(exc))

    # Instagram Reels
    if upload_cfg.get("instagram_reels") and not has_been_uploaded(
        db_path, row_id, "instagram_reels"
    ):
        try:
            ig_id = upload_reel(
                processed_path,
                caption=caption,
                hashtags=["clips", "gaming", source, streamer.replace(" ", "")],
            )
            record_upload(db_path, row_id, "instagram_reels", post_id=ig_id)
        except Exception as exc:
            logger.error("Instagram Reels upload failed: %s", exc)
            record_upload(
                db_path, row_id, "instagram_reels", status="failed", error=str(exc)
            )

    # Clean up processed file after all uploads
    if os.path.exists(processed_path):
        os.remove(processed_path)
        logger.debug("Cleaned up: %s", processed_path)


def run_pipeline(config_path: str = "config.yaml", dry_run: bool = False) -> None:
    cfg = load_config(config_path)
    db_path: str = cfg["paths"]["database"]
    clip_cfg = cfg["clips"]

    ensure_dir(cfg["paths"]["downloads"])
    ensure_dir(cfg["paths"]["output"])
    init_db(db_path)

    limit = clip_cfg.get("limit_per_streamer", 5)
    min_views = clip_cfg.get("min_views", 0)
    max_duration = clip_cfg.get("max_duration_seconds", 60)
    period = clip_cfg.get("period", "week")

    total_processed = 0

    # ── Twitch ──────────────────────────────────────────────────────────────
    for streamer_entry in cfg.get("streamers", {}).get("twitch", []):
        login = streamer_entry.get("login", "")
        if not login:
            continue
        logger.info("Fetching Twitch clips for: %s", login)
        try:
            clips = get_twitch_clips(
                login,
                limit=limit,
                period=period,
                min_views=min_views,
                max_duration=max_duration,
            )
        except Exception as exc:
            logger.error("Failed to fetch Twitch clips for %s: %s", login, exc)
            continue

        for clip in clips:
            if is_clip_known(db_path, "twitch", clip.clip_id):
                logger.debug("Skipping known clip: %s", clip.clip_id)
                continue
            _process_and_upload(
                cfg=cfg,
                db_path=db_path,
                source="twitch",
                clip_id=clip.clip_id,
                clip_url=clip.url,
                streamer=clip.broadcaster_name,
                title=clip.title,
                view_count=clip.view_count,
                duration=clip.duration,
                dry_run=dry_run,
            )
            total_processed += 1

    # ── YouTube ─────────────────────────────────────────────────────────────
    for channel_entry in cfg.get("streamers", {}).get("youtube", []):
        channel_id = channel_entry.get("channel_id", "")
        if not channel_id:
            continue
        logger.info("Fetching YouTube clips for channel: %s", channel_id)
        try:
            clips = get_youtube_clips(
                channel_id,
                limit=limit,
                period=period,
                min_views=min_views,
                max_duration=max_duration,
            )
        except Exception as exc:
            logger.error("Failed to fetch YouTube clips for %s: %s", channel_id, exc)
            continue

        for clip in clips:
            if is_clip_known(db_path, "youtube", clip.clip_id):
                logger.debug("Skipping known clip: %s", clip.clip_id)
                continue
            _process_and_upload(
                cfg=cfg,
                db_path=db_path,
                source="youtube",
                clip_id=clip.clip_id,
                clip_url=clip.url,
                streamer=clip.channel_title,
                title=clip.title,
                view_count=clip.view_count,
                duration=clip.duration_seconds,
                dry_run=dry_run,
            )
            total_processed += 1

    logger.info("Pipeline complete. Clips processed this run: %d", total_processed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="fabz- Stream Clipping Bot")
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process clips but skip uploading",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING)"
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    run_pipeline(config_path=args.config, dry_run=args.dry_run)
