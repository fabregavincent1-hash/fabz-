"""
Caption generator — transcribes audio with OpenAI Whisper and burns
subtitles into the video via ffmpeg.

Usage:
    from processor.caption_generator import add_captions
    output = add_captions("input.mp4", "output_captioned.mp4")
"""

import logging
import os
import subprocess
import tempfile
from typing import Optional

from utils.helpers import optional_env

logger = logging.getLogger(__name__)


def _get_whisper_model() -> str:
    return optional_env("OPENAI_WHISPER_MODEL", "base")


def transcribe(video_path: str, srt_output_path: Optional[str] = None) -> str:
    """
    Run Whisper on a video file and produce an SRT subtitle file.

    Returns the path to the generated .srt file.
    Raises RuntimeError if transcription fails.
    """
    model = _get_whisper_model()

    if srt_output_path is None:
        srt_output_path = video_path.rsplit(".", 1)[0] + ".srt"

    srt_dir = os.path.dirname(os.path.abspath(srt_output_path)) or "."
    srt_base = os.path.splitext(os.path.basename(srt_output_path))[0]

    # Extract audio first for faster Whisper processing
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
        audio_path = tmp_audio.name

    try:
        extract_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            audio_path,
        ]
        result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {result.stderr[-300:]}")

        # Run Whisper CLI
        whisper_cmd = [
            "whisper",
            audio_path,
            "--model", model,
            "--output_format", "srt",
            "--output_dir", srt_dir,
            "--language", "en",
            "--task", "transcribe",
        ]
        result = subprocess.run(whisper_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"Whisper failed: {result.stderr[-300:]}")

        # Whisper saves as <audio_basename>.srt in output_dir
        audio_base = os.path.splitext(os.path.basename(audio_path))[0]
        generated_srt = os.path.join(srt_dir, f"{audio_base}.srt")

        if not os.path.exists(generated_srt):
            raise RuntimeError(f"Whisper SRT not found at {generated_srt}")

        # Rename to desired output path if different
        if generated_srt != srt_output_path:
            os.replace(generated_srt, srt_output_path)

        logger.info("Transcription complete: %s", srt_output_path)
        return srt_output_path

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


def burn_captions(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_size: int = 24,
    font_color: str = "white",
    outline_color: str = "black",
) -> str:
    """
    Burn subtitles from an SRT file into the video using ffmpeg.

    Returns output_path.
    """
    # Escape path for ffmpeg subtitles filter (Windows-safe too)
    escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")

    subtitle_style = (
        f"Fontsize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"   # white
        f"OutlineColour=&H00000000,"   # black outline
        f"Outline=2,"
        f"Alignment=2"                 # center bottom
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles='{escaped_srt}':force_style='{subtitle_style}'",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle burn failed: {result.stderr[-500:]}")

    logger.info("Captions burned in: %s", output_path)
    return output_path


def add_captions(video_path: str, output_path: str) -> str:
    """
    Convenience function: transcribe + burn captions in one call.

    Returns output_path.
    """
    srt_path = video_path.rsplit(".", 1)[0] + "_captions.srt"
    try:
        transcribe(video_path, srt_path)
        burn_captions(video_path, srt_path, output_path)
    finally:
        if os.path.exists(srt_path):
            os.unlink(srt_path)
    return output_path
