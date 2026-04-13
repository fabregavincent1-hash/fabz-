"""
Video processor — downloads a clip and reformats it for short-form platforms.

Pipeline per clip:
  1. download_clip()        — yt-dlp downloads the source video
  2. reformat_to_vertical() — ffmpeg crops/pads to 9:16 (1080×1920)
  3. add_branding()         — ffmpeg overlays channel name / logo
  4. Returns final output path ready for upload
"""

import logging
import os
import subprocess
from pathlib import Path

import ffmpeg

from utils.helpers import safe_filename, ensure_dir

logger = logging.getLogger(__name__)


def download_clip(url: str, download_dir: str, filename_hint: str = "") -> str:
    """
    Download a clip from any yt-dlp-supported URL.

    Returns the absolute path of the downloaded file.
    Raises RuntimeError if download fails.
    """
    ensure_dir(download_dir)
    safe_hint = safe_filename(filename_hint) if filename_hint else "clip"
    out_template = os.path.join(download_dir, f"{safe_hint}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", out_template,
        "--quiet",
        "--no-warnings",
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed for {url}: {result.stderr.strip()}"
        )

    # Find the downloaded file
    for f in Path(download_dir).iterdir():
        if f.stem == safe_hint and f.suffix == ".mp4":
            logger.info("Downloaded: %s", f)
            return str(f)

    # Fallback: find most recently modified mp4 in dir
    mp4_files = sorted(
        Path(download_dir).glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True
    )
    if mp4_files:
        logger.info("Downloaded (fallback): %s", mp4_files[0])
        return str(mp4_files[0])

    raise RuntimeError(f"Downloaded file not found in {download_dir}")


def reformat_to_vertical(input_path: str, output_path: str) -> str:
    """
    Reformat a video to 9:16 vertical (1080×1920) using ffmpeg.

    Strategy: smart crop — detect content area, then pad to fill 1080×1920
    with blurred background fill (looks better than plain black bars).

    Returns output_path.
    """
    try:
        probe = ffmpeg.probe(input_path)
        video_stream = next(
            s for s in probe["streams"] if s["codec_type"] == "video"
        )
        w = int(video_stream["width"])
        h = int(video_stream["height"])
    except Exception as exc:
        raise RuntimeError(f"Failed to probe video {input_path}: {exc}") from exc

    target_w, target_h = 1080, 1920
    target_ratio = target_w / target_h   # 0.5625

    source_ratio = w / h

    # Build ffmpeg filter graph
    if source_ratio > target_ratio:
        # Landscape source → crop width, then scale
        # Crop to correct aspect ratio centered
        new_w = int(h * target_ratio)
        crop_x = (w - new_w) // 2
        filter_complex = (
            f"[0:v]crop={new_w}:{h}:{crop_x}:0,"
            f"scale={target_w}:{target_h}[main];"
            # Blurred background
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},"
            f"boxblur=20:5[bg];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[out]"
        )
    else:
        # Vertical or square source → scale and pad
        filter_complex = (
            f"[0:v]scale={target_w}:-2[main];"
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},"
            f"boxblur=20:5[bg];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg reformat failed: {result.stderr[-500:]}"
        )

    logger.info("Reformatted to vertical: %s", output_path)
    return output_path


def add_branding(
    input_path: str,
    output_path: str,
    channel_name: str,
    watermark_position: str = "top-right",
    logo_path: str = "",
) -> str:
    """
    Burn a channel name watermark (and optional logo) onto the video.

    Returns output_path.
    """
    position_map = {
        "top-left":     ("20", "20"),
        "top-right":    ("W-tw-20", "20"),
        "bottom-left":  ("20", "H-th-20"),
        "bottom-right": ("W-tw-20", "H-th-20"),
    }
    x, y = position_map.get(watermark_position, ("W-tw-20", "20"))

    if logo_path and os.path.isfile(logo_path):
        # Overlay logo image + text
        filter_graph = (
            f"[1:v]scale=80:-1[logo];"
            f"[0:v][logo]overlay={x}:{y}[tmp];"
            f"[tmp]drawtext=text='{channel_name}':fontsize=36:fontcolor=white"
            f":borderw=2:bordercolor=black:x={x}:y={y}+90[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", logo_path,
            "-filter_complex", filter_graph,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # Text-only watermark
        safe_name = channel_name.replace("'", "\\'")
        filter_graph = (
            f"drawtext=text='{safe_name}':fontsize=40:fontcolor=white"
            f":borderw=2:bordercolor=black:x={x}:y={y}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", filter_graph,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg branding failed: {result.stderr[-500:]}"
        )

    logger.info("Branding applied: %s", output_path)
    return output_path


def process_clip(
    url: str,
    download_dir: str,
    output_dir: str,
    title: str,
    channel_name: str,
    watermark_position: str = "top-right",
    logo_path: str = "",
    vertical: bool = True,
) -> str:
    """
    Full processing pipeline: download → reformat → brand.

    Returns the path to the final processed video.
    """
    ensure_dir(download_dir)
    ensure_dir(output_dir)

    safe_title = safe_filename(title or "clip")

    raw_path = download_clip(url, download_dir, filename_hint=safe_title)

    if vertical:
        vertical_path = os.path.join(output_dir, f"{safe_title}_vertical.mp4")
        reformat_to_vertical(raw_path, vertical_path)
        base_path = vertical_path
    else:
        base_path = raw_path

    branded_path = os.path.join(output_dir, f"{safe_title}_final.mp4")
    add_branding(
        base_path,
        branded_path,
        channel_name=channel_name,
        watermark_position=watermark_position,
        logo_path=logo_path,
    )

    # Clean up intermediate files
    if vertical and os.path.exists(vertical_path) and vertical_path != branded_path:
        os.remove(vertical_path)
    if os.path.exists(raw_path) and raw_path != branded_path:
        os.remove(raw_path)

    return branded_path
