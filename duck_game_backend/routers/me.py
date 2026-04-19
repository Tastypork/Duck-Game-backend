from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from duck_game_backend.config import Settings, get_settings
from duck_game_backend.database import get_db
from duck_game_backend.game.service import DuckGameService
from duck_game_backend.routers._deps import (
    Identity,
    guild_header,
    identity_headers,
    require_user_id,
)

router = APIRouter(tags=["me"])


@router.get("/me")
def get_me(
    uid: str = Depends(require_user_id),
    guild: str = Depends(guild_header),
    identity: Identity = Depends(identity_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    svc.cache_user_identity(
        guild,
        uid,
        username=identity.username,
        global_name=identity.global_name,
        avatar_hash=identity.avatar_hash,
    )
    return svc.user_me_payload(guild, uid)
