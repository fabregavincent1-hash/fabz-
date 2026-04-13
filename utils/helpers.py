"""
Shared utility functions: logging setup, config loading, file helpers.
"""

import logging
import os
import shutil
import yaml
from dotenv import load_dotenv
from pathlib import Path
from typing import Any

# Load .env on first import
load_dotenv()


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def clean_dir(path: str) -> None:
    """Remove all files inside a directory without deleting the directory itself."""
    for item in Path(path).iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


def safe_filename(name: str, max_len: int = 80) -> str:
    """Convert a string into a filesystem-safe filename."""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_- ")
    cleaned = "".join(c if c in keep else "_" for c in name)
    return cleaned[:max_len].strip()


def require_env(key: str) -> str:
    """Get a required environment variable or raise a clear error."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your credentials."
        )
    return value


def optional_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)
