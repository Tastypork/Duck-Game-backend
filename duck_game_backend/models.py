from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DuckRow(Base):
    __tablename__ = "ducks"

    guild_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    duck_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    rarity: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    attack: Mapped[int] = mapped_column(Integer, nullable=False)
    defense: Mapped[int] = mapped_column(Integer, nullable=False)
    speed: Mapped[int] = mapped_column(Integer, nullable=False)
    shiny: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserRow(Base):
    __tablename__ = "users"

    guild_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ducks_json: Mapped[str] = mapped_column(Text, nullable=False)
    last_catch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cooldown: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keish_bonus_rolls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Zay encounter in progress when ``zay_next_round`` is non-NULL.
    zay_next_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zay_snapshot_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zay_discord_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    zay_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Cached Discord identity — populated from request headers to avoid hitting Discord.
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    global_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ``!battle`` hourly limit (UTC hour bucket) + win streak within that hour.
    battle_hour_bucket: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battle_streak: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )


class BotAnnouncementRow(Base):
    __tablename__ = "bot_announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivered_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class PendingRevengeRow(Base):
    __tablename__ = "pending_revenge"
    __table_args__ = (Index("ix_pending_revenge_guild_victim", "guild_id", "victim_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(32), nullable=False)
    victim_id: Mapped[str] = mapped_column(String(32), nullable=False)
    thief_id: Mapped[str] = mapped_column(String(32), nullable=False)
    stolen_duck_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
