from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files_to_load() -> tuple[Path, ...]:
    """Pick `.env` paths so DISCORD_* load under systemd, editable installs, and plain `pip install`.

    Candidates (in order, deduped, existing files only):
    - ``DUCK_GAME_BACKEND_ENV_FILE`` (absolute path) — useful when `.env` is not next to source.
    - Project root (directory containing ``pyproject.toml``).
    - ``cwd`` — matches systemd ``WorkingDirectory``.
    """
    candidates: list[Path] = []
    override = (os.environ.get("DUCK_GAME_BACKEND_ENV_FILE") or "").strip()
    if override:
        candidates.append(Path(override).expanduser())

    pkg_root = Path(__file__).resolve().parent.parent
    if (pkg_root / "pyproject.toml").is_file():
        candidates.append(pkg_root / ".env")

    candidates.append(Path.cwd() / ".env")

    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in candidates:
        try:
            key = p.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return tuple(p for p in uniq if p.is_file())


_ENV_FILES = _env_files_to_load()


def env_files_loaded() -> tuple[str, ...]:
    """Paths of `.env` files passed to pydantic (surfaced by ``GET /`` diagnostics)."""
    return tuple(str(p) for p in _ENV_FILES)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES if _ENV_FILES else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://duck:duck@localhost:5432/duckgame"
    # Upstream duck image API: GET returns ``{"url": "<image link>"}``.
    duck_image_api_url: str = "https://duck.jocal.dev/duck"
    dashboard_base_url: str = "https://api.duckgame.app/user"
    # Origin clients/Discord use for ``/static/game/*`` embed URLs.
    public_base_url: str = "https://api.duckgame.app"
    # If set (e.g. "/api"), JSON routes are mounted under that prefix.
    api_prefix: str = ""
    # Shared secret for the ``/v1/bot/*`` poll endpoints. Empty => endpoints return 404.
    bot_shared_secret: str = ""
    # Discord OAuth2 credentials for ``POST /oauth/token``.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    # Bot token used by ``POST /oauth/discord/shared-guilds`` to intersect guild lists.
    discord_bot_token: str = ""
    names_common_path: Path = Path(__file__).resolve().parent / "data" / "names_common.json"
    names_legendary_path: Path = Path(__file__).resolve().parent / "data" / "names_legendary.json"
    # Where ``GET /user/{id}`` writes the generated HTML page before serving it.
    user_html_dir: Path = Path(__file__).resolve().parent / "static" / "user_html"
    # Comma-separated extra hostnames allowed through ``GET /v1/image-proxy``.
    # Discord CDNs and the hosts in the URLs above are always allowed.
    image_proxy_allowed_hosts: str = ""


def normalize_api_prefix(raw: str) -> str:
    p = (raw or "").strip().rstrip("/")
    if not p:
        return ""
    return p if p.startswith("/") else f"/{p}"


def get_settings() -> Settings:
    return Settings()
