from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from duck_game_backend.config import Settings, get_settings
from duck_game_backend.models import Base


def make_engine(settings: Settings | None = None):
    settings = settings or get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, echo=False)


_engine = None
_SessionLocal = None


# Columns added to ``users`` after the initial schema. Each entry is
# ``(column_name, sqlite_ddl, postgres_ddl)``. ``create_all`` does not alter
# existing tables, so we add missing columns here at startup.
_USERS_COLUMN_ADDS: list[tuple[str, str, str]] = [
    (
        "keish_bonus_rolls",
        "INTEGER NOT NULL DEFAULT 0",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    ("zay_next_round", "INTEGER", "INTEGER"),
    ("zay_snapshot_n", "INTEGER", "INTEGER"),
    ("zay_discord_guild_id", "BIGINT", "BIGINT"),
    ("zay_channel_id", "BIGINT", "BIGINT"),
    ("username", "VARCHAR(128)", "VARCHAR(128)"),
    ("global_name", "VARCHAR(128)", "VARCHAR(128)"),
    ("avatar_hash", "VARCHAR(128)", "VARCHAR(128)"),
    ("battle_hour_bucket", "BIGINT", "BIGINT"),
    (
        "battle_streak",
        "BOOLEAN NOT NULL DEFAULT 0",
        "BOOLEAN NOT NULL DEFAULT FALSE",
    ),
]


def _apply_users_column_adds(engine) -> None:
    insp = inspect(engine)
    try:
        existing = {c["name"] for c in insp.get_columns("users")}
    except Exception:
        return
    is_pg = engine.dialect.name == "postgresql"
    stmts = [
        f"ALTER TABLE users ADD COLUMN {name} {pg_ddl if is_pg else sqlite_ddl}"
        for name, sqlite_ddl, pg_ddl in _USERS_COLUMN_ADDS
        if name not in existing
    ]
    if not stmts:
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def _widen_duck_id_columns(engine) -> None:
    """Widen duck_id / stolen_duck_id from VARCHAR(64) → VARCHAR(256) on Postgres."""
    if engine.dialect.name != "postgresql":
        return
    insp = inspect(engine)
    try:
        tables = insp.get_table_names()
    except Exception:
        return
    with engine.begin() as conn:
        if "ducks" in tables:
            r = conn.execute(
                text(
                    """
                    SELECT character_maximum_length FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'ducks' AND column_name = 'duck_id'
                    """
                )
            ).scalar()
            if r is not None and r < 256:
                conn.execute(text("ALTER TABLE ducks ALTER COLUMN duck_id TYPE VARCHAR(256)"))
        if "pending_revenge" in tables:
            r = conn.execute(
                text(
                    """
                    SELECT character_maximum_length FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'pending_revenge'
                    AND column_name = 'stolen_duck_id'
                    """
                )
            ).scalar()
            if r is not None and r < 256:
                conn.execute(
                    text("ALTER TABLE pending_revenge ALTER COLUMN stolen_duck_id TYPE VARCHAR(256)")
                )


def init_db(settings: Settings | None = None) -> None:
    global _engine, _SessionLocal
    settings = settings or get_settings()
    _engine = make_engine(settings)
    Base.metadata.create_all(bind=_engine)
    _widen_duck_id_columns(_engine)
    _apply_users_column_adds(_engine)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_session_factory():
    if _SessionLocal is None:
        init_db()
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
