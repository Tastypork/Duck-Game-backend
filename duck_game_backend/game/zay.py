"""Zay encounter state + resolution. Backend-only; returns effect dicts for clients."""

from __future__ import annotations

import logging
import math
import random
from typing import TYPE_CHECKING, Any

from duck_game_backend.game import constants as C
from duck_game_backend.models import UserRow

if TYPE_CHECKING:
    from duck_game_backend.game.service import DuckGameService

LOGGER = logging.getLogger("duck_game.zay")


def _steal_budget_for_round(round_index: int, snapshot_n: int) -> int:
    if round_index == 1:
        return 10 + math.ceil(0.015 * snapshot_n)
    if round_index == 2:
        return 6 + math.ceil(0.01 * snapshot_n)
    return 3 + math.ceil(0.006 * snapshot_n)


class ZayBackend:
    """Zay encounter state lives on ``UserRow`` so it survives across HTTP requests."""

    __slots__ = ("_service",)

    def __init__(self, service: DuckGameService) -> None:
        self._service = service

    def _ensure_user_row(self, guild_id: str, user_id: str) -> UserRow:
        row = self._service.get_user(guild_id, user_id)
        if row is not None:
            return row
        self._service.session.add(
            UserRow(
                guild_id=guild_id,
                user_id=user_id,
                ducks_json="[]",
                last_catch=None,
                cooldown=None,
                keish_bonus_rolls=0,
            )
        )
        self._service.session.commit()
        out = self._service.get_user(guild_id, user_id)
        assert out is not None
        return out

    def active(self, guild_id: str, user_id: str) -> bool:
        row = self._service.get_user(guild_id, user_id)
        return row is not None and row.zay_next_round is not None

    def _clear_user(self, guild_id: str, user_id: str) -> None:
        row = self._service.get_user(guild_id, user_id)
        if row is None:
            return
        row.zay_next_round = None
        row.zay_snapshot_n = None
        row.zay_discord_guild_id = None
        row.zay_channel_id = None
        self._service.session.commit()

    def start(self, guild_id: str, user_id: str, guild_id_int: int, channel_id: int) -> None:
        self._clear_user(guild_id, user_id)
        row = self._ensure_user_row(guild_id, user_id)
        row.zay_next_round = 1
        row.zay_snapshot_n = len(self._service.loss_eligible_duck_ids(guild_id, user_id))
        row.zay_discord_guild_id = guild_id_int
        row.zay_channel_id = channel_id
        self._service.session.commit()

    def handle_defense_attempt(self, guild_id: str, user_id: str) -> list[dict[str, Any]]:
        """Returns UI effects: reply embeds and/or channel broadcasts."""
        row = self._service.get_user(guild_id, user_id)
        if row is None or row.zay_next_round is None:
            return []

        rnd = row.zay_next_round
        if rnd not in (1, 2, 3):
            LOGGER.warning("[zay] invalid round %s for user %s; clearing state", rnd, user_id)
            self._clear_user(guild_id, user_id)
            return [{"kind": "zay_cleared_invalid"}]

        snapshot_n = row.zay_snapshot_n or 0
        defend_p = C.ZAY_DEFENSE_PROBS[rnd - 1]
        defended = random.random() < defend_p

        if not defended:
            return self._finish_steal(guild_id, user_id, rnd, snapshot_n)

        if rnd >= 3:
            return self._finish_full_defense(guild_id, user_id)

        row.zay_next_round = rnd + 1
        self._service.session.commit()
        embed = {
            "title": C.ZAY_MID_DEFENSE_TITLE,
            "description": C.ZAY_MID_DEFENSE_DESCRIPTION,
            "color": 16753920,
            "image_url": self._service.asset_url("clash.png"),
            "footer": C.ZAY_MID_DEFENSE_FOOTER,
        }
        return [{"kind": "zay_reply", "embed": embed}]

    def _finish_steal(
        self, guild_id: str, user_id: str, failed_round: int, snapshot_n: int
    ) -> list[dict[str, Any]]:
        row = self._service.get_user(guild_id, user_id)
        if row is None or row.zay_discord_guild_id is None or row.zay_channel_id is None:
            return []
        guild_id_int = int(row.zay_discord_guild_id)
        channel_id = int(row.zay_channel_id)
        budget = _steal_budget_for_round(failed_round, snapshot_n)
        eligible = self._service.loss_eligible_duck_ids(guild_id, user_id)
        remove_n = min(budget, len(eligible))
        lost_names: list[str] = []
        if remove_n > 0:
            random.shuffle(eligible)
            for duck_id in eligible[:remove_n]:
                drow = self._service.get_duck(guild_id, duck_id)
                if drow:
                    lost_names.append(str(drow.name))
                self._service.obliterate_duck(guild_id, user_id, duck_id)

        self._clear_user(guild_id, user_id)

        mention = f"<@{user_id}>"
        names_bit = ""
        if lost_names:
            preview = ", ".join(lost_names[:8])
            if len(lost_names) > 8:
                preview += f", +{len(lost_names) - 8} more"
            names_bit = f"\n\n**Taken:** {preview}"

        if remove_n > 0:
            desc = (
                f"{mention} **While he was stealing, you couldn't stop every grab.** "
                f"**{remove_n}** duck{'s' if remove_n != 1 else ''} are gone.{names_bit}"
            )
            color = 10038562
        else:
            desc = (
                f"{mention} **Zay came for your flock, but nothing he could take was on the table.** "
                "**Legendary** and **Mythic** ducks are untouchable."
            )
            color = 3066993

        embed = {
            "title": C.ZAY_STEAL_FINALE_TITLE,
            "description": desc,
            "color": color,
            "footer": "You go back to strengthen your defenses",
            "image_url": self._service.asset_url("zay_success.png"),
        }
        return [
            {
                "kind": "zay_channel_broadcast",
                "guild_id": str(guild_id_int),
                "channel_id": str(channel_id),
                "embed": embed,
            }
        ]

    def _finish_full_defense(self, guild_id: str, user_id: str) -> list[dict[str, Any]]:
        row = self._service.get_user(guild_id, user_id)
        if row is None or row.zay_discord_guild_id is None or row.zay_channel_id is None:
            return []
        guild_id_int = int(row.zay_discord_guild_id)
        channel_id = int(row.zay_channel_id)
        snapshot_n = row.zay_snapshot_n or 0
        self._clear_user(guild_id, user_id)

        mention = f"<@{user_id}>"
        if snapshot_n == 0:
            desc = (
                f"{mention} **Zay couldn't steal any of your birds.** "
                "**Legendary** and **Mythic** ducks are untouchable — nothing else was on the table either."
            )
        else:
            desc = f"{mention} **Zay couldn't steal any of your birds.**"
        embed = {
            "title": C.ZAY_FULL_DEFENSE_TITLE,
            "description": desc,
            "color": 3066993,
            "footer": "Your flock's safe — for now.",
            "image_url": self._service.asset_url("zay_defeat.png"),
        }
        return [
            {
                "kind": "zay_channel_broadcast",
                "guild_id": str(guild_id_int),
                "channel_id": str(channel_id),
                "embed": embed,
            }
        ]
