"""Email provider factory for runtime assembly."""

from __future__ import annotations

import os

from .email_provider import EmailProvider, MockEmailProvider
from .runtime_env import load_server_env


def create_email_provider() -> EmailProvider:
    load_server_env()
    provider = os.environ.get("WISPERA_EMAIL_PROVIDER", "mock").strip().lower()
    if provider in {"", "mock"}:
        return MockEmailProvider()
    raise RuntimeError(f"unsupported WISPERA_EMAIL_PROVIDER: {provider}")
