"""Discord OAuth2: token exchange, user profile, and shared-guild intersection."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from duck_game_backend.config import Settings, get_settings

LOGGER = logging.getLogger("duck_game.oauth")

router = APIRouter(tags=["oauth"])

_DISCORD_API = "https://discord.com/api/v10"


def _discord_fetch_all_guilds(client: httpx.Client, authorization: str) -> list[dict[str, Any]]:
    """GET /users/@me/guilds with pagination (200 per page)."""
    out: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 200}
        if after is not None:
            params["after"] = after
        r = client.get(
            f"{_DISCORD_API}/users/@me/guilds",
            headers={"Authorization": authorization},
            params=params,
            timeout=30.0,
        )
        if r.status_code >= 400:
            try:
                payload = r.json()
                err = payload.get("message") or payload.get("error") or r.text
            except ValueError:
                err = r.text
            raise HTTPException(status_code=400, detail=str(err)[:500])
        batch = r.json()
        if not isinstance(batch, list):
            LOGGER.warning("Discord guilds non-list: %s", str(batch)[:200])
            break
        out.extend(batch)
        if len(batch) < 200:
            break
        last = batch[-1]
        if not isinstance(last, dict) or not last.get("id"):
            break
        after = str(last["id"])
    return out


class OAuthTokenBody(BaseModel):
    code: str = Field(..., min_length=1)
    # Required for browser OAuth, omitted for Embedded SDK token exchange.
    redirect_uri: str | None = None


class AccessTokenBody(BaseModel):
    access_token: str = Field(..., min_length=1)


@router.post("/oauth/token")
def oauth_token(
    body: OAuthTokenBody,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    cid = (settings.discord_client_id or "").strip()
    secret = (settings.discord_client_secret or "").strip()
    if not cid or not secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "Discord OAuth is not configured: set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET "
                "on Duck-Game-backend (same application as VITE_DISCORD_CLIENT_ID in the Activity)."
            ),
        )
    data: dict[str, str] = {
        "client_id": cid,
        "client_secret": secret,
        "grant_type": "authorization_code",
        "code": body.code.strip(),
    }
    if body.redirect_uri and body.redirect_uri.strip():
        data["redirect_uri"] = body.redirect_uri.strip()

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                "https://discord.com/api/oauth2/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as e:
        LOGGER.exception("Discord token request failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach Discord OAuth") from e

    try:
        payload = r.json()
    except ValueError:
        LOGGER.warning("Discord token non-JSON: %s", r.text[:300])
        raise HTTPException(status_code=502, detail="Invalid Discord OAuth response")

    if r.status_code >= 400:
        err = payload.get("error_description") or payload.get("error") or r.text
        LOGGER.warning("Discord token error %s: %s", r.status_code, err)
        raise HTTPException(status_code=400, detail=str(err)[:500])

    access = payload.get("access_token")
    if not access or not isinstance(access, str):
        raise HTTPException(status_code=502, detail="Discord OAuth response missing access_token")

    return {"access_token": access}


@router.post("/oauth/discord/profile")
def oauth_discord_profile(body: AccessTokenBody) -> dict[str, str | None]:
    """Resolve Discord user for browser OAuth (avoids CORS on ``GET discord.com/api/@me`` from the SPA)."""
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {body.access_token.strip()}"},
            )
    except httpx.RequestError as e:
        LOGGER.exception("Discord @me request failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach Discord API") from e

    try:
        payload = r.json()
    except ValueError:
        LOGGER.warning("Discord @me non-JSON: %s", r.text[:300])
        raise HTTPException(status_code=502, detail="Invalid Discord API response")

    if r.status_code >= 400:
        err = payload.get("message") or payload.get("error") or r.text
        LOGGER.warning("Discord @me error %s: %s", r.status_code, err)
        raise HTTPException(status_code=400, detail=str(err)[:500])

    uid = payload.get("id")
    if not uid or not isinstance(uid, str):
        raise HTTPException(status_code=502, detail="Discord @me missing id")

    return {
        "id": uid,
        "username": payload.get("username") if isinstance(payload.get("username"), str) else None,
        "global_name": payload.get("global_name") if isinstance(payload.get("global_name"), str) else None,
        "avatar": payload.get("avatar") if isinstance(payload.get("avatar"), str) else None,
    }


class SharedGuildOut(BaseModel):
    id: str
    name: str
    icon: str | None = None


@router.post("/oauth/discord/shared-guilds", response_model=list[SharedGuildOut])
def oauth_discord_shared_guilds(
    body: AccessTokenBody,
    settings: Settings = Depends(get_settings),
) -> list[SharedGuildOut]:
    """Guilds the OAuth user shares with this application's bot (intersection)."""
    bot_token = (settings.discord_bot_token or "").strip()
    if not bot_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "DISCORD_BOT_TOKEN is not set on Duck-Game-backend — cannot list servers the bot is in."
            ),
        )
    user_token = body.access_token.strip()
    try:
        with httpx.Client(timeout=30.0) as client:
            user_guilds = _discord_fetch_all_guilds(client, f"Bearer {user_token}")
            bot_guilds = _discord_fetch_all_guilds(client, f"Bot {bot_token}")
    except HTTPException:
        raise
    except httpx.RequestError as e:
        LOGGER.exception("Discord guilds request failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach Discord API") from e

    bot_ids = {g["id"] for g in bot_guilds if isinstance(g, dict) and g.get("id")}
    shared: list[dict[str, Any]] = [
        g for g in user_guilds if isinstance(g, dict) and str(g.get("id", "")) in bot_ids
    ]
    shared.sort(key=lambda x: str(x.get("name") or "").lower())

    out: list[SharedGuildOut] = []
    for g in shared:
        gid = g.get("id")
        name = g.get("name")
        if not gid or not isinstance(gid, str):
            continue
        icon = g.get("icon") if isinstance(g.get("icon"), str) else None
        out.append(
            SharedGuildOut(
                id=gid,
                name=name if isinstance(name, str) else "Unknown server",
                icon=icon,
            )
        )
    return out
