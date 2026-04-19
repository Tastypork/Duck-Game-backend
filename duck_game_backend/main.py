from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from duck_game_backend.config import env_files_loaded, get_settings, normalize_api_prefix
from duck_game_backend.database import init_db
from duck_game_backend.routers import bot as bot_router
from duck_game_backend.routers import game as game_router
from duck_game_backend.routers import me as me_router
from duck_game_backend.routers import oauth as oauth_router
from duck_game_backend.routers import user_dashboard as user_dashboard_router

_STATIC_GAME = Path(__file__).resolve().parent / "static" / "game"
_STATIC_GAME.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(get_settings())
    yield


_settings = get_settings()
_API_PREFIX = normalize_api_prefix(_settings.api_prefix)

app = FastAPI(title="Duck Game API", lifespan=lifespan)

# `allow_credentials=True` with `allow_origins=["*"]` is invalid per CORS and
# browsers reject cross-origin requests. This API uses header auth, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


for _r in (me_router.router, game_router.router, bot_router.router, oauth_router.router):
    app.include_router(_r, prefix=_API_PREFIX)

# Public HTML duck pages at /user/{id}; also under API_PREFIX when set so that
# clients using duck_game_api_url like https://host/api get the same URLs.
app.include_router(user_dashboard_router.router)
if _API_PREFIX:
    app.include_router(user_dashboard_router.router, prefix=_API_PREFIX)

# Serve /static/game/... (embed images). Mirror under the API prefix so clients
# that reach the API via https://host/api still get working asset URLs.
_GAME_STATIC = StaticFiles(directory=str(_STATIC_GAME))
app.mount("/static/game", _GAME_STATIC, name="game_assets")
if _API_PREFIX:
    app.mount(f"{_API_PREFIX}/static/game", _GAME_STATIC, name="game_assets_prefixed")


@app.get("/")
def root():
    """Identify this service — ``curl https://your-host/`` confirms the bot points at Duck-Game-backend."""
    cid = (_settings.discord_client_id or "").strip()
    secret = (_settings.discord_client_secret or "").strip()
    bot_tok = (_settings.discord_bot_token or "").strip()
    return {
        "service": "duck-game-backend",
        "api_prefix": _API_PREFIX or "/",
        "docs": "/docs",
        "discord_oauth_configured": bool(cid and secret),
        "discord_bot_token_configured": bool(bot_tok),
        # Paths pydantic read (empty tuple => only OS env was used).
        "env_files_loaded": list(env_files_loaded()),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
