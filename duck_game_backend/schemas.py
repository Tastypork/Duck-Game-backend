from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DuckCatchBody(BaseModel):
    guild_id: int | None = None
    channel_id: int | None = None
    source: Literal["web", "bot"] | None = None


class DuckBattleBody(BaseModel):
    guild_id: int | None = None


class GiveBody(BaseModel):
    receiver_id: str = Field(..., min_length=1)
    duck_name: str = Field(..., min_length=1)


class ReleaseBody(BaseModel):
    duck_name: str = Field(..., min_length=1)


class DuckCatchResponse(BaseModel):
    result: dict[str, Any]


class AnnouncementAckBody(BaseModel):
    ids: list[int] = Field(default_factory=list)
