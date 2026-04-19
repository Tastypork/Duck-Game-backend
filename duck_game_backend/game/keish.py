from __future__ import annotations

from typing import Any

from duck_game_backend.game.constants import KEISH_BONUS_ROLLS
from duck_game_backend.models import UserRow


class KeishEnergy:
    """Keish bonus duck rolls, persisted on ``UserRow`` so state survives HTTP requests."""

    __slots__ = ("_svc",)

    def __init__(self, service: Any) -> None:
        self._svc = service

    def rolls_remaining(self, guild_id: str, user_id: str) -> int:
        row = self._svc.get_user(guild_id, user_id)
        return row.keish_bonus_rolls if row is not None else 0

    def has_pending_rolls(self, guild_id: str, user_id: str) -> bool:
        return self.rolls_remaining(guild_id, user_id) > 0

    def grant_rolls(self, guild_id: str, user_id: str, n: int = KEISH_BONUS_ROLLS) -> None:
        session = self._svc.session
        row = self._svc.get_user(guild_id, user_id)
        if row is None:
            session.add(
                UserRow(
                    guild_id=guild_id,
                    user_id=user_id,
                    ducks_json="[]",
                    last_catch=None,
                    cooldown=None,
                    keish_bonus_rolls=n,
                )
            )
        else:
            row.keish_bonus_rolls = n
        session.commit()

    def consume_one_roll(self, guild_id: str, user_id: str) -> int:
        """Decrement bonus rolls by one; return rolls remaining after this catch."""
        row = self._svc.get_user(guild_id, user_id)
        if row is None or row.keish_bonus_rolls <= 0:
            return 0
        row.keish_bonus_rolls -= 1
        remaining = row.keish_bonus_rolls
        self._svc.session.commit()
        return remaining
