from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from duck_game_backend.config import Settings, get_settings
from duck_game_backend.database import get_db
from duck_game_backend.game.service import DuckGameService
from duck_game_backend.guild_id import normalize_guild_id
from duck_game_backend.routers._deps import (
    Identity,
    guild_header,
    identity_headers,
    optional_user_id,
    require_user_id,
    resolve_guild_int,
)
from duck_game_backend.schemas import (
    DuckBattleBody,
    DuckCatchBody,
    DuckCatchResponse,
    GiveBody,
    LeaderboardBody,
    ReleaseBody,
)

LOGGER = logging.getLogger("duck_game.routers.game")

router = APIRouter(tags=["game"])


# Outcomes worth announcing in the Discord channel when a catch came from the
# web Activity. Must match ``kind`` values in ``DuckGameService.duck_catch``.
# Cooldown/error are excluded to avoid spam; theft is embedded in ``catch``.
_ANNOUNCEABLE_KINDS = {
    "catch",
    "keish_proc",
    "keish_catch",
    "zay_proc",
    "zay_effects",
    "boot",
    "revenge_battle",
}


def _image_proxy_host_allowlist(settings: Settings) -> frozenset[str]:
    hosts: set[str] = {"cdn.discordapp.com", "media.discordapp.net"}
    for raw in (
        settings.duck_image_api_url,
        settings.public_base_url,
        settings.dashboard_base_url,
    ):
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            h = urlparse(raw.strip()).hostname
        except Exception:
            continue
        if h:
            hosts.add(h.lower())
    extra = (settings.image_proxy_allowed_hosts or "").strip()
    if extra:
        for part in extra.split(","):
            p = part.strip().lower()
            if p:
                hosts.add(p)
    return frozenset(hosts)


@router.get("/v1/image-proxy")
def image_proxy(url: str, settings: Settings = Depends(get_settings)):
    """Fetch an image server-side so the strict-CSP Discord Activity iframe can display it via same-origin ``/api``."""
    raw = (url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="missing url")
    try:
        parsed = urlparse(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail="bad url") from e
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="only http(s) URLs")
    host = (parsed.hostname or "").lower()
    if not host or host not in _image_proxy_host_allowlist(settings):
        raise HTTPException(status_code=403, detail="host not allowed for image proxy")
    try:
        r = httpx.get(raw, timeout=20.0, follow_redirects=True)
    except httpx.HTTPError:
        LOGGER.exception("image proxy fetch failed: %s", raw[:120])
        raise HTTPException(status_code=502, detail="upstream fetch failed") from None
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"upstream {r.status_code}")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type") or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/v1/duck/catch", response_model=DuckCatchResponse)
def duck_catch(
    body: DuckCatchBody,
    x_guild_id: str | None = Header(default=None, alias="x-guild-id"),
    uid: str = Depends(require_user_id),
    identity: Identity = Depends(identity_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    guild_int = resolve_guild_int(body.guild_id, x_guild_id)
    guild_str = normalize_guild_id(guild_int) if guild_int is not None else normalize_guild_id(x_guild_id)
    svc.cache_user_identity(
        guild_str,
        uid,
        username=identity.username,
        global_name=identity.global_name,
        avatar_hash=identity.avatar_hash,
    )
    steal_ids: set[str] | None = None
    if body.guild_member_ids is not None:
        steal_ids = {str(i) for i in body.guild_member_ids}
    result = svc.duck_catch(uid, guild_int, body.channel_id, steal_eligible_user_ids=steal_ids)
    if (
        body.source == "web"
        and guild_int is not None
        and body.channel_id is not None
        and isinstance(result, dict)
        and result.get("kind") in _ANNOUNCEABLE_KINDS
    ):
        try:
            svc.enqueue_bot_announcement(
                guild_id=guild_int,
                channel_id=body.channel_id,
                user_id=uid,
                payload=result,
            )
        except Exception:
            LOGGER.exception("failed to enqueue bot announcement for web catch")
    return DuckCatchResponse(result=result)


@router.post("/v1/duck/battle", response_model=DuckCatchResponse)
def duck_battle(
    body: DuckBattleBody,
    x_guild_id: str | None = Header(default=None, alias="x-guild-id"),
    uid: str = Depends(require_user_id),
    identity: Identity = Depends(identity_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    guild_int = resolve_guild_int(body.guild_id, x_guild_id)
    if guild_int is None:
        raise HTTPException(
            status_code=400,
            detail="Missing guild (set x-guild-id header or guild_id in body)",
        )
    guild_str = normalize_guild_id(guild_int)
    svc.cache_user_identity(
        guild_str,
        uid,
        username=identity.username,
        global_name=identity.global_name,
        avatar_hash=identity.avatar_hash,
    )
    steal_ids: set[str] | None = None
    if body.guild_member_ids is not None:
        steal_ids = {str(i) for i in body.guild_member_ids}
    return DuckCatchResponse(result=svc.duck_battle(uid, guild_int, steal_eligible_user_ids=steal_ids))


@router.get("/leaderboard")
def leaderboard(
    uid: str | None = Depends(optional_user_id),
    guild: str = Depends(guild_header),
    identity: Identity = Depends(identity_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    if uid:
        svc.cache_user_identity(
            guild,
            uid,
            username=identity.username,
            global_name=identity.global_name,
            avatar_hash=identity.avatar_hash,
        )
    return svc.leaderboard(guild)


@router.post("/v1/leaderboard")
def leaderboard_filtered(
    body: LeaderboardBody,
    uid: str | None = Depends(optional_user_id),
    guild: str = Depends(guild_header),
    identity: Identity = Depends(identity_headers),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Top 10 among ``guild_member_ids`` when provided (POST body avoids huge query strings)."""
    svc = DuckGameService(db, settings)
    if uid:
        svc.cache_user_identity(
            guild,
            uid,
            username=identity.username,
            global_name=identity.global_name,
            avatar_hash=identity.avatar_hash,
        )
    eligible: set[str] | None = None
    if body.guild_member_ids is not None:
        eligible = {str(i) for i in body.guild_member_ids}
    return svc.leaderboard(guild, eligible_user_ids=eligible)


@router.post("/v1/ducks/give")
def give_duck(
    body: GiveBody,
    uid: str = Depends(require_user_id),
    guild: str = Depends(guild_header),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    out = svc.give_duck(guild, uid, body.receiver_id.strip(), body.duck_name.strip())
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("message", "Give failed"))
    return out


@router.post("/v1/ducks/release")
def release_duck(
    body: ReleaseBody,
    uid: str = Depends(require_user_id),
    guild: str = Depends(guild_header),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    svc = DuckGameService(db, settings)
    out = svc.release_duck(guild, uid, body.duck_name.strip())
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("message", "Release failed"))
    return out


@router.get("/v1/dashboard-url/{target_user_id}")
def dashboard_url(
    target_user_id: str,
    guild: int | None = Query(
        default=None,
        description="Discord server id for the HTML page inventory bucket (defaults to 0).",
    ),
    settings: Settings = Depends(get_settings),
):
    base = settings.dashboard_base_url.rstrip("/")
    url = f"{base}/{target_user_id.strip()}"
    if guild is not None:
        url = f"{url}?guild={int(guild)}"
    return {"url": url}
