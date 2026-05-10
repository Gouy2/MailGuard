"""Runtime environment helpers for the Wispera server."""

from __future__ import annotations

import os
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
SERVER_ENV_PATH = SERVER_ROOT / ".env"
_SERVER_ENV_LOADED = False


def load_server_env() -> None:
    """Load server/.env once without overriding existing environment values."""
    global _SERVER_ENV_LOADED
    if _SERVER_ENV_LOADED:
        return
    if not SERVER_ENV_PATH.exists():
        _SERVER_ENV_LOADED = True
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_env_fallback(SERVER_ENV_PATH)
    else:
        load_dotenv(SERVER_ENV_PATH)
    _SERVER_ENV_LOADED = True


def _load_env_fallback(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_flag(name: str) -> bool:
    load_server_env()
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}
