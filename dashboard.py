"""
fabz- Web Dashboard
====================
A local web UI for controlling the clipping bot.

Usage:
    pip install flask
    python dashboard.py
    # Open http://localhost:5000 in your browser
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import yaml
from flask import Flask, Response, jsonify, render_template, request
from dotenv import load_dotenv, set_key

load_dotenv()

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
ENV_PATH = BASE_DIR / ".env"

app = Flask(__name__)

# ── Scheduler process handle ─────────────────────────────────────────────────
_scheduler_proc: subprocess.Popen | None = None
_scheduler_lock = threading.Lock()


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── Status ────────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    with _scheduler_lock:
        running = _scheduler_proc is not None and _scheduler_proc.poll() is None

    twitch_ok = bool(os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET"))
    youtube_ok = bool(os.getenv("YOUTUBE_API_KEY"))

    return jsonify(
        scheduler_running=running,
        twitch_ok=twitch_ok if (twitch_ok or youtube_ok) else None,
        youtube_ok=youtube_ok if (twitch_ok or youtube_ok) else None,
    )


# ── Streamer config ───────────────────────────────────────────────────────────
@app.route("/api/streamers", methods=["GET"])
def get_streamers():
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        streamers_cfg = cfg.get("streamers", {})
        result = []
        for s in streamers_cfg.get("twitch", []):
            result.append({"platform": "twitch", "login": s.get("login", "")})
        for s in streamers_cfg.get("youtube", []):
            result.append({"platform": "youtube", "channel_id": s.get("channel_id", "")})
        return jsonify(streamers=result)
    except Exception as exc:
        return jsonify(streamers=[], error=str(exc))


@app.route("/api/streamers", methods=["POST"])
def save_streamers():
    try:
        data = request.get_json()
        streamers = data.get("streamers", [])

        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)

        cfg.setdefault("streamers", {})
        cfg["streamers"]["twitch"] = [
            {"login": s["login"]} for s in streamers if s.get("platform") == "twitch"
        ]
        cfg["streamers"]["youtube"] = [
            {"channel_id": s["channel_id"]}
            for s in streamers
            if s.get("platform") == "youtube"
        ]

        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


# ── Env / API keys ────────────────────────────────────────────────────────────
ALLOWED_ENV_KEYS = {
    "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN",
    "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID",
    "OPENAI_WHISPER_MODEL",
}


@app.route("/api/env", methods=["GET"])
def get_env_hints():
    """Return which keys are already set (not the values)."""
    set_keys = [k for k in ALLOWED_ENV_KEYS if os.getenv(k)]
    return jsonify(keys=set_keys)


@app.route("/api/env", methods=["POST"])
def save_env():
    try:
        data = request.get_json()
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        for key, value in data.items():
            if key not in ALLOWED_ENV_KEYS:
                continue
            set_key(str(ENV_PATH), key, value)
            os.environ[key] = value  # apply immediately to current process
        load_dotenv(override=True)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc))


# ── Streaming command runner ──────────────────────────────────────────────────
def _stream_subprocess(cmd: list[str]):
    """Generator: yields SSE-formatted lines from a subprocess."""
    def _encode(text: str, type_: str = "info") -> str:
        payload = json.dumps({"text": text.rstrip(), "type": type_})
        return f"data: {payload}\n\n"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(BASE_DIR),
        )
        for line in proc.stdout:
            yield _encode(line)
        proc.wait()
        success = proc.returncode == 0
    except Exception as exc:
        yield _encode(str(exc), "error")
        success = False

    yield f"data: {json.dumps({'type': 'done', 'success': success})}\n\n"


@app.route("/api/run/<action>")
def api_run(action: str):
    action_map = {
        "install": [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
        "check":   [sys.executable, "-c", _CHECK_SCRIPT],
        "run":     [sys.executable, "main.py"],
        "dryrun":  [sys.executable, "main.py", "--dry-run"],
    }
    cmd = action_map.get(action)
    if not cmd:
        return jsonify(error="Unknown action"), 400

    return Response(
        _stream_subprocess(cmd),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Scheduler start/stop ──────────────────────────────────────────────────────
@app.route("/api/scheduler/start", methods=["POST"])
def scheduler_start():
    global _scheduler_proc
    with _scheduler_lock:
        if _scheduler_proc and _scheduler_proc.poll() is None:
            return jsonify(ok=True, message="Already running")
        _scheduler_proc = subprocess.Popen(
            [sys.executable, "scheduler.py", "--run-now"],
            cwd=str(BASE_DIR),
        )
    return jsonify(ok=True)


@app.route("/api/scheduler/stop", methods=["POST"])
def scheduler_stop():
    global _scheduler_proc
    with _scheduler_lock:
        if _scheduler_proc:
            _scheduler_proc.terminate()
            try:
                _scheduler_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _scheduler_proc.kill()
            _scheduler_proc = None
    return jsonify(ok=True)


# ── System check script (run as subprocess) ───────────────────────────────────
_CHECK_SCRIPT = """
import shutil, sys, subprocess, os
from dotenv import load_dotenv
load_dotenv()

ok = True

# Python version
print(f"Python: {sys.version.split()[0]}")

# ffmpeg
if shutil.which("ffmpeg"):
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    ver = r.stdout.split("\\n")[0]
    print(f"ffmpeg: OK — {ver}")
else:
    print("ERROR: ffmpeg not found. Install with: sudo apt install ffmpeg")
    ok = False

# yt-dlp
if shutil.which("yt-dlp"):
    print("yt-dlp: OK")
else:
    try:
        import yt_dlp
        print("yt-dlp: OK (python package)")
    except ImportError:
        print("WARNING: yt-dlp not found. Run Install Dependencies.")
        ok = False

# whisper
try:
    import whisper
    print("openai-whisper: OK")
except ImportError:
    print("WARNING: openai-whisper not installed. Run Install Dependencies.")

# API keys
keys = {
    "TWITCH_CLIENT_ID": "Twitch",
    "YOUTUBE_API_KEY": "YouTube",
    "TIKTOK_ACCESS_TOKEN": "TikTok",
    "INSTAGRAM_ACCESS_TOKEN": "Instagram",
}
for k, name in keys.items():
    val = os.getenv(k)
    print(f"{name} API key: {'SET' if val else 'MISSING — add in API Keys section'}")

if ok:
    print("\\nAll required system dependencies found.")
else:
    print("\\nSome issues found — see above.")
"""


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print("  fabz- Dashboard")
    print("  ───────────────────────────────")
    print(f"  Running on port {port}")
    print()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
