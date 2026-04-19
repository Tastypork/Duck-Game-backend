"""Internal poll/ack endpoints consumed by the Discord bot to relay web-origin catches."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from duck_game_backend.config import Settings, get_settings
from duck_game_backend.database import get_db
from duck_game_backend.game.clock import utc_ts
from duck_game_backend.models import BotAnnouncementRow
from duck_game_backend.schemas import AnnouncementAckBody

router = APIRouter(tags=["bot"], prefix="/v1/bot")


def _require_bot(settings: Settings, x_bot_token: str | None) -> None:
    """Opt-in bot guard: returns 404 if ``bot_shared_secret`` is unset, 401 on mismatch."""
    secret = (settings.bot_shared_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_bot_token or x_bot_token.strip() != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/pending-announcements")
def pending_announcements(
    limit: int = Query(default=20, ge=1, le=100),
    x_bot_token: str | None = Header(default=None, alias="x-bot-token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _require_bot(settings, x_bot_token)
    rows = (
        db.execute(
            select(BotAnnouncementRow)
            .where(BotAnnouncementRow.delivered_at.is_(None))
            .order_by(BotAnnouncementRow.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "guild_id": int(r.guild_id),
                "channel_id": int(r.channel_id),
                "user_id": r.user_id,
                "payload": json.loads(r.payload_json) if r.payload_json else {},
                "created_at": int(r.created_at),
            }
            for r in rows
        ]
    }


@router.post("/announcements/ack")
def ack_announcements(
    body: AnnouncementAckBody,
    x_bot_token: str | None = Header(default=None, alias="x-bot-token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _require_bot(settings, x_bot_token)
    ids = [int(i) for i in body.ids if isinstance(i, int) or str(i).isdigit()]
    if not ids:
        return {"acked": 0}
    now = utc_ts()
    result = db.execute(
        update(BotAnnouncementRow)
        .where(
            BotAnnouncementRow.id.in_(ids),
            BotAnnouncementRow.delivered_at.is_(None),
        )
        .values(delivered_at=now)
    )
    db.commit()
    return {"acked": int(result.rowcount or 0)}
