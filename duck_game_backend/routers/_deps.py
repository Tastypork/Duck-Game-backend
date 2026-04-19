"""Shared FastAPI header dependencies for routers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from duck_game_backend.guild_id import normalize_guild_id


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def require_user_id(x_user_id: str | None = Header(default=None, alias="x-user-id")) -> str:
    uid = _clean(x_user_id)
    if not uid:
        raise HTTPException(status_code=400, detail="Missing x-user-id header")
    return uid


def optional_user_id(x_user_id: str | None = Header(default=None, alias="x-user-id")) -> str | None:
    return _clean(x_user_id)


def guild_header(x_guild_id: str | None = Header(default=None, alias="x-guild-id")) -> str:
    """Normalized guild scope (`"0"` when absent)."""
    return normalize_guild_id(_clean(x_guild_id))


def guild_int_from_header(x_guild_id: str | None) -> int | None:
    """Parse ``x-guild-id`` as int; raise 400 on bad value, return None when absent."""
    s = _clean(x_guild_id)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid x-guild-id header") from e


def resolve_guild_int(body_guild: int | None, x_guild_id: str | None) -> int | None:
    """Body-supplied guild takes precedence over the header."""
    if body_guild is not None:
        return body_guild
    return guild_int_from_header(x_guild_id)


@dataclass(frozen=True)
class Identity:
    """Cached Discord identity forwarded by clients on every request."""

    username: str | None
    global_name: str | None
    avatar_hash: str | None


def identity_headers(
    x_user_name: str | None = Header(default=None, alias="x-user-name"),
    x_user_global_name: str | None = Header(default=None, alias="x-user-global-name"),
    x_user_avatar: str | None = Header(default=None, alias="x-user-avatar"),
) -> Identity:
    return Identity(
        username=_clean(x_user_name),
        global_name=_clean(x_user_global_name),
        avatar_hash=_clean(x_user_avatar),
    )
