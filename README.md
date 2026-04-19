# Duck-Game-backend

FastAPI service powering the duck-catching game shared by the Fishin-Tiffin Discord bot and the Duck-Game web/Activity client.

## Quick start (dev)

1. Start Postgres (e.g. from `Duck-Game/docker-compose.yml`: `docker compose up -d db`).
2. Install (editable) into a virtualenv:

   ```bash
   cd Duck-Game-backend
   python -m venv .venv && source .venv/bin/activate
   pip install -e .
   ```

3. Copy `.env.example` to `.env` and fill in values (at minimum `DATABASE_URL`; see [Configuration](#configuration)).
4. Run:

   ```bash
   uvicorn duck_game_backend.main:app --reload --host 0.0.0.0 --port 19999
   ```

5. Sanity check:

   ```bash
   curl -s http://127.0.0.1:19999/           # { "service": "duck-game-backend", ... }
   curl -s http://127.0.0.1:19999/health     # { "status": "ok" }
   ```

### Port in use?

The default (`19999`) is a high port to avoid clashes. If it's taken, pick another and use the same value everywhere: the `uvicorn --port` flag, `PORT` env (Docker), and `PUBLIC_BASE_URL` (including the port in the URL). To see what's listening: `ss -tulpn | grep LISTEN`.

## Docker

```bash
docker build -t duck-game-backend .
docker run --rm -p 19999:19999 --env-file .env duck-game-backend
```

`PORT` (env) overrides the default.

## Configuration

All settings load from the environment and/or a `.env` file. The file is looked up in (first match wins):

1. `DUCK_GAME_BACKEND_ENV_FILE` (absolute path — useful under systemd).
2. Project root (directory containing `pyproject.toml`).
3. Current working directory.

OS env always layers on top, so `Environment=` in a unit file still wins.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://duck:duck@localhost:5432/duckgame` | SQLAlchemy DSN. |
| `PUBLIC_BASE_URL` | `https://api.duckgame.app` | Origin clients/Discord use for `/static/game/*` embed URLs. |
| `DUCK_IMAGE_API_URL` | `https://duck.jocal.dev/duck` | Upstream duck image API (`GET` returns `{"url": "..."}`). |
| `DASHBOARD_BASE_URL` | `https://api.duckgame.app/user` | Base URL returned by `GET /v1/dashboard-url/{user_id}`. |
| `API_PREFIX` | *(empty)* | If set (e.g. `/api`), all JSON and HTML routes are also mounted under that prefix, plus a parallel `/{prefix}/static/game` mount. |
| `USER_HTML_DIR` | `duck_game_backend/static/user_html` | Where `GET /user/{id}` writes the generated HTML page before serving it. |
| `IMAGE_PROXY_ALLOWED_HOSTS` | *(empty)* | Comma-separated extra hostnames allowed through `/v1/image-proxy`. Discord CDNs and the hosts of the URLs above are always allowed. |
| `DISCORD_CLIENT_ID` | *(empty)* | Required for `POST /oauth/token` (otherwise **503**). Must match `VITE_DISCORD_CLIENT_ID` in the Activity client. |
| `DISCORD_CLIENT_SECRET` | *(empty)* | Paired with `DISCORD_CLIENT_ID`. |
| `DISCORD_BOT_TOKEN` | *(empty)* | Required for `POST /oauth/discord/shared-guilds` (otherwise **503**). |
| `BOT_SHARED_SECRET` | *(empty)* | Gate for the internal `/v1/bot/*` poll endpoints. Empty => those endpoints return **404**. |

Static game assets (GIFs/PNGs for Keish/Zay and the boot art) live in `duck_game_backend/static/game/` and are served at `GET /static/game/<filename>`. Catch responses include absolute `image_url` fields built from `PUBLIC_BASE_URL`; set it to the origin Discord/browsers actually use to reach this API.

## API

### Public

- `GET /` — service identification + diagnostic flags (OAuth configured, env files loaded).
- `GET /health` — liveness probe.
- `GET /me` — current user payload. Headers: `x-user-id`, `x-guild-id`, `x-user-name`, `x-user-global-name`, `x-user-avatar`.
- `GET /leaderboard` — top 10 + total. Same headers as `/me` (all optional except guild scoping).
- `POST /v1/duck/catch` — main `!duck` flow. Headers: `x-user-id` (required), plus the guild/identity headers above. Body: `{ "guild_id"?: int, "channel_id"?: int, "source"?: "web" | "bot" }`. When `source=web` and both guild/channel ids are supplied, announceable outcomes are queued for the Discord bot via `/v1/bot/*`.
- `POST /v1/duck/battle` — random PvP battle. Requires a guild (header or body).
- `POST /v1/ducks/give` — `{ "receiver_id": str, "duck_name": str }` + `x-user-id`.
- `POST /v1/ducks/release` — `{ "duck_name": str }` + `x-user-id`.
- `GET /v1/dashboard-url/{user_id}?guild=<snowflake>` — returns the public dashboard URL for a user.
- `GET /user/{user_id}?guild=<snowflake>` — renders (and caches to `USER_HTML_DIR`) an HTML page with the user's collection.
- `GET /v1/image-proxy?url=<absolute_url>` — same-origin image proxy for the Discord Activity iframe (strict CSP). Restricted to the host allowlist.

### Discord OAuth

- `POST /oauth/token` — exchanges an authorize-code for an access token. Returns **503** if `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET` are unset.
- `POST /oauth/discord/profile` — resolves a Discord user from an access token (avoids CORS on `GET discord.com/api/@me` from the SPA).
- `POST /oauth/discord/shared-guilds` — guilds the user shares with this app's bot. Returns **503** without `DISCORD_BOT_TOKEN`.

### Bot-internal (opt-in via `BOT_SHARED_SECRET`)

All require header `x-bot-token: <BOT_SHARED_SECRET>`; return **404** when the secret is unset.

- `GET /v1/bot/pending-announcements?limit=<1..100>` — queued catch outcomes to post.
- `POST /v1/bot/announcements/ack` — `{ "ids": [int, ...] }` marks them delivered.

Interactive docs are available at `/docs` (Swagger UI) and `/redoc`.

## Troubleshooting

**`404` on `POST /v1/duck/catch`.** If the response looks like `{"message":"Route POST:...","statusCode":404}` (not FastAPI's `{"detail":"Not Found"}`), the request is hitting a different service. Confirm with `curl -s https://YOUR_HOST/` — it should return `"service":"duck-game-backend"`. If you serve under a subpath, set `API_PREFIX=/api` and point the bot (`duck_game_api_url` in Fishin `config.yml`) at `https://host/api`.

**`discord_oauth_configured: false` on `GET /`.** `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET` did not load. Check the `env_files_loaded` array in the same response — if it's empty, either point `DUCK_GAME_BACKEND_ENV_FILE` at an absolute path or set the vars directly in your process environment (systemd `Environment=` / `EnvironmentFile=`), then restart.
