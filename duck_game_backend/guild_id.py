"""Discord guild / server scope for multi-tenant game data."""

from __future__ import annotations


def normalize_guild_id(guild_id: int | str | None) -> str:
    """Snowflake string per guild. ``None`` / empty => ``"0"`` (DM / legacy single-bucket)."""
    if guild_id is None:
        return "0"
    s = str(guild_id).strip()
    return s if s else "0"
