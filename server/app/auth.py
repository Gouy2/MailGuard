"""Optional API authentication for the MailGuard server."""

from __future__ import annotations

import os

try:
    from fastapi import Request
except ImportError:  # pragma: no cover - lets non-server unit tests import this module without FastAPI.
    class Request:  # type: ignore[no-redef]
        pass

from .runtime_env import load_server_env


def configured_auth_token() -> str:
    load_server_env()
    return os.environ.get("MAILGUARD_AUTH_TOKEN", "").strip()


async def require_api_token(request: Request) -> None:
    token = configured_auth_token()
    if not token:
        return

    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or value.strip() != token:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
