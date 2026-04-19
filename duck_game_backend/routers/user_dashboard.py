"""Public HTML duck collection pages: write to disk then serve (GET /user/...)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from duck_game_backend.config import Settings, get_settings
from duck_game_backend.database import get_db
from duck_game_backend.game.duck_dashboard_html import generate_duck_dashboard_html
from duck_game_backend.game.service import DuckGameService
from duck_game_backend.guild_id import normalize_guild_id

router = APIRouter(tags=["user-dashboard"])


def _validate_snowflake(raw: str, label: str) -> str:
    s = raw.strip()
    if not s.isdigit() or len(s) < 5 or len(s) > 22:
        raise HTTPException(status_code=400, detail=f"invalid {label}")
    return s


def _display_name_for_user(svc: DuckGameService, guild_id: str, user_id: str) -> str:
    row = svc.get_user(guild_id, user_id)
    if not row:
        return user_id
    return (row.global_name or row.username or user_id).strip() or user_id


@router.get("/user/{user_id}")
def user_dashboard_html(
    user_id: str,
    guild: int | None = Query(
        default=None,
        description="Discord server id; defaults to 0 (DM / legacy single bucket).",
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Build HTML from DB, write under ``user_html_dir``, return same bytes to the client."""
    uid = _validate_snowflake(user_id, "user id")
    gid = normalize_guild_id(guild)
    svc = DuckGameService(db, settings)
    payload = svc.user_me_payload(gid, uid)
    display = _display_name_for_user(svc, gid, uid)
    ducks = payload.get("ducks") or []
    if not isinstance(ducks, list):
        ducks = []

    out_dir: Path = settings.user_html_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{gid}_{uid}.html"
    generate_duck_dashboard_html(
        user_display_name=display,
        ducks=ducks,
        output_path=out_path,
    )
    html_out = out_path.read_text(encoding="utf-8")

    return HTMLResponse(content=html_out)
