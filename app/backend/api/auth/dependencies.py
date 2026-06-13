"""Supabase JWT middleware — pass-through until AUTH_REQUIRED=true.

Wired in day one so routes can carry the `Depends(get_current_user)` annotation
from the start, and flipping the env var enables enforcement without rewrites.
"""
from fastapi import Header, HTTPException
from supabase import create_client

from settings import settings


def _client():
    if not (settings.SUPABASE_URL and settings.SUPABASE_ANON_KEY):
        return None
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


async def get_current_user(authorization: str | None = Header(default=None)):
    """Returns user when a valid JWT is provided, else None.

    While AUTH_REQUIRED=false, the function is a no-op pass-through.
    """
    if not settings.AUTH_REQUIRED:
        return None
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    client = _client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Auth is enabled but Supabase env vars are not configured",
        )

    try:
        token = authorization.replace("Bearer ", "", 1)
        return client.auth.get_user(token)
    except Exception as exc:  # noqa: BLE001 — upstream exception surface is broad
        raise HTTPException(status_code=401, detail="Invalid token") from exc
