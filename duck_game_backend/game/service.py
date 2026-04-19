"""Core duck game logic: catch/battle/give/release + Keish & Zay event handling."""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from duck_game_backend.config import Settings
from duck_game_backend.game import constants as C
from duck_game_backend.game.clock import utc_ts
from duck_game_backend.game.keish import KeishEnergy
from duck_game_backend.game.zay import ZayBackend
from duck_game_backend.guild_id import normalize_guild_id
from duck_game_backend.models import BotAnnouncementRow, DuckRow, PendingRevengeRow, UserRow

LOGGER = logging.getLogger("duck_game.service")


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    if m <= 0:
        return f"{s}s"
    if s == 0:
        return f"{m}m"
    return f"{m}m {s}s"


def _generate_duck_id() -> str:
    return str(uuid.uuid4())


def _load_names(path) -> list[str]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, list) else []


def _roll_rarity() -> str:
    labels, weights = zip(*C.RARITY_WEIGHTS)
    return random.choices(labels, weights=weights, k=1)[0]


def _roll_stat(rarity: str) -> int:
    weights = C.STAT_WEIGHTS[rarity]
    vals = list(range(1, 11))
    return random.choices(vals, weights=weights, k=1)[0]


def _roll_shiny() -> bool:
    return random.uniform(0, 100) < C.SHINY_PROB


def _roll_duck_outcome(*, allow_zay_proc: bool, allow_keish_proc: bool, allow_boot: bool) -> str:
    labels = []
    weights = []
    for label, weight in C.DUCK_OUTCOME_WEIGHTS:
        if label == "zay_proc" and not allow_zay_proc:
            continue
        if label == "keish_proc" and not allow_keish_proc:
            continue
        if label == "boot" and not allow_boot:
            continue
        labels.append(label)
        weights.append(weight)
    return random.choices(labels, weights=weights, k=1)[0]


def _roll_cooldown_seconds() -> int:
    val = random.gauss(C.COOLDOWN_MEAN, C.COOLDOWN_STD)
    val = max(C.COOLDOWN_MIN, min(C.COOLDOWN_MAX, val))
    return int(round(val))


def _random_battle_flavor(winner_name: str, loser_name: str, margin: int) -> str:
    intense = margin >= C.REVENGE_SWING_STEAL_THRESHOLD
    lines = [
        f"{winner_name} launches a perfectly timed wing combo and outpaces {loser_name}.",
        f"A storm of feathers erupts as {winner_name} overwhelms {loser_name}.",
        f"{winner_name} reads every move, then counters {loser_name} with a final splash.",
        f"{winner_name} circles high, dives hard, and breaks through {loser_name}'s guard.",
        f"{winner_name} ducks low and lands the deciding hit on {loser_name}.",
    ]
    if intense:
        lines.extend(
            [
                f"{winner_name} absolutely dominates the pond and leaves {loser_name} reeling.",
                f"{winner_name} unleashes an unstoppable barrage while {loser_name} cannot respond.",
            ]
        )
    return random.choice(lines)


class DuckGameService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.keish = KeishEnergy(self)
        self.zay = ZayBackend(self)

    def asset_url(self, filename: str) -> str:
        """Public URL for files under /static/game/ (see duck_game_backend/static/game/)."""
        base = self.settings.public_base_url.rstrip("/")
        return f"{base}/static/game/{filename}"

    def pick_name_for_rarity(self, rarity: str) -> str:
        path = (
            self.settings.names_common_path
            if rarity in ("Common", "Uncommon", "Rare")
            else self.settings.names_legendary_path
        )
        names = _load_names(path)
        if not names:
            raise RuntimeError(f"No names available in {path.name}. Add more names to continue.")
        return random.choice(names)

    def get_user(self, guild_id: str, user_id: str) -> UserRow | None:
        return self.session.get(UserRow, (guild_id, user_id))

    def set_user(
        self,
        guild_id: str,
        user_id: str,
        ducks_list: list[str],
        last_catch: int | None,
        cooldown: int | None,
    ) -> None:
        row = self.session.get(UserRow, (guild_id, user_id))
        payload = json.dumps(ducks_list)
        if row is None:
            self.session.add(
                UserRow(
                    guild_id=guild_id,
                    user_id=user_id,
                    ducks_json=payload,
                    last_catch=last_catch,
                    cooldown=cooldown,
                    keish_bonus_rolls=0,
                )
            )
        else:
            row.ducks_json = payload
            row.last_catch = last_catch
            row.cooldown = cooldown
        self.session.commit()

    def add_duck_to_user(self, guild_id: str, user_id: str, duck_id: str) -> None:
        row = self.get_user(guild_id, user_id)
        ducks_list: list[str] = []
        if row:
            ducks_list = json.loads(row.ducks_json) if row.ducks_json else []
        if duck_id not in ducks_list:
            ducks_list.append(duck_id)
        self.set_user(guild_id, user_id, ducks_list, row.last_catch if row else None, row.cooldown if row else None)

    def remove_duck_from_user(self, guild_id: str, user_id: str, duck_id: str) -> None:
        row = self.get_user(guild_id, user_id)
        if not row:
            return
        ducks_list = json.loads(row.ducks_json) if row.ducks_json else []
        if duck_id in ducks_list:
            ducks_list.remove(duck_id)
        self.set_user(guild_id, user_id, ducks_list, row.last_catch, row.cooldown)

    def get_user_duck_ids(self, guild_id: str, user_id: str) -> list[str]:
        row = self.get_user(guild_id, user_id)
        if not row or not row.ducks_json:
            return []
        return json.loads(row.ducks_json)

    def get_duck(self, guild_id: str, duck_id: str) -> DuckRow | None:
        return self.session.get(DuckRow, (guild_id, duck_id))

    def is_stealable_duck(self, duck_row: DuckRow | None) -> bool:
        if duck_row is None:
            return False
        if duck_row.shiny:
            return False
        return duck_row.rarity not in ("Legendary", "Mythic")

    def get_random_stealable_duck_from_user(
        self,
        guild_id: str,
        user_id: str,
        exclude_duck_ids: set[str] | None = None,
    ) -> str | None:
        exclude_duck_ids = exclude_duck_ids or set()
        eligible: list[str] = []
        for duck_id in self.get_user_duck_ids(guild_id, user_id):
            if duck_id in exclude_duck_ids:
                continue
            duck_row = self.get_duck(guild_id, duck_id)
            if duck_row and self.is_stealable_duck(duck_row):
                eligible.append(duck_id)
        if not eligible:
            return None
        return random.choice(eligible)

    def get_random_user_with_stealable_ducks(
        self, guild_id: str, exclude_user_id: str | None = None
    ) -> tuple[str, str] | None:
        rows = self.session.execute(select(UserRow).where(UserRow.guild_id == guild_id)).scalars().all()
        candidates: list[tuple[str, list[str]]] = []
        for row in rows:
            uid = row.user_id
            if exclude_user_id and uid == exclude_user_id:
                continue
            duck_ids = json.loads(row.ducks_json) if row.ducks_json else []
            eligible_ids: list[str] = []
            for duck_id in duck_ids:
                dr = self.get_duck(guild_id, duck_id)
                if dr and self.is_stealable_duck(dr):
                    eligible_ids.append(duck_id)
            if eligible_ids:
                candidates.append((uid, eligible_ids))
        if not candidates:
            return None
        random_user_id, duck_ids = random.choice(candidates)
        return (random_user_id, random.choice(duck_ids))

    def loss_eligible_duck_ids(self, guild_id: str, user_id: str) -> list[str]:
        out: list[str] = []
        for duck_id in self.get_user_duck_ids(guild_id, user_id):
            row = self.get_duck(guild_id, duck_id)
            if row and row.rarity not in ("Legendary", "Mythic"):
                out.append(duck_id)
        return out

    def fetch_duck_url(self) -> tuple[str, int]:
        """Call the upstream duck image API and return ``(image_url, unix_ts)``."""
        ts = utc_ts()
        with httpx.Client(timeout=15.0) as client:
            r = client.get(self.settings.duck_image_api_url.rstrip("/"))
            r.raise_for_status()
            url = r.json().get("url")
            if not url:
                raise RuntimeError("Duck image API returned no 'url' field.")
            return url, ts

    def create_duck_record(self, guild_id: str, duck_id: str, url: str, timestamp: int) -> DuckRow:
        rarity = _roll_rarity()
        attack = _roll_stat(rarity)
        defense = _roll_stat(rarity)
        speed = _roll_stat(rarity)
        shiny = _roll_shiny()
        name = self.pick_name_for_rarity(rarity)
        if shiny:
            name = f"✨{name}✨"
        row = DuckRow(
            guild_id=guild_id,
            duck_id=duck_id,
            url=url,
            rarity=rarity,
            name=name,
            attack=attack,
            defense=defense,
            speed=speed,
            shiny=shiny,
            timestamp=timestamp,
            owner_id=None,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def set_duck_owner(self, guild_id: str, duck_id: str, owner_id: str) -> None:
        row = self.get_duck(guild_id, duck_id)
        if row:
            row.owner_id = owner_id
            self.session.commit()

    def update_cooldown(self, guild_id: str, user_id: str) -> int:
        cd = _roll_cooldown_seconds()
        now_ts = utc_ts()
        row = self.get_user(guild_id, user_id)
        ducks_list = json.loads(row.ducks_json) if row and row.ducks_json else []
        self.set_user(guild_id, user_id, ducks_list, now_ts, cd)
        return cd

    def refresh_user_cooldown(self, guild_id: str, user_id: str) -> None:
        row = self.get_user(guild_id, user_id)
        ducks_list = json.loads(row.ducks_json) if row and row.ducks_json else []
        self.set_user(guild_id, user_id, ducks_list, None, None)

    def obliterate_duck(self, guild_id: str, user_id: str, duck_id: str) -> None:
        self.remove_duck_from_user(guild_id, user_id, duck_id)
        self.session.execute(
            delete(DuckRow).where(DuckRow.guild_id == guild_id, DuckRow.duck_id == duck_id)
        )
        self.session.commit()

    def check_on_cooldown(self, guild_id: str, user_id: str) -> tuple[bool, int]:
        if self.zay.active(guild_id, user_id):
            return False, 0
        row = self.get_user(guild_id, user_id)
        if not row or not row.last_catch or not row.cooldown:
            return False, 0
        now_ts = utc_ts()
        ready_ts = row.last_catch + row.cooldown
        if now_ts < ready_ts:
            return True, ready_ts - now_ts
        return False, 0

    def set_pending_revenge(
        self, guild_id: str, victim_id: str, thief_id: str, stolen_duck_id: str
    ) -> None:
        now_ts = utc_ts()
        expires_ts = now_ts + C.REVENGE_WINDOW_SECONDS
        self.session.add(
            PendingRevengeRow(
                guild_id=guild_id,
                victim_id=victim_id,
                thief_id=thief_id,
                stolen_duck_id=stolen_duck_id,
                created_ts=now_ts,
                expires_ts=expires_ts,
            )
        )
        self.session.commit()

    def get_pending_revenge(self, guild_id: str, victim_id: str) -> PendingRevengeRow | None:
        now_ts = utc_ts()
        self.session.execute(
            delete(PendingRevengeRow).where(PendingRevengeRow.expires_ts < now_ts)
        )
        row = self.session.execute(
            select(PendingRevengeRow)
            .where(
                PendingRevengeRow.guild_id == guild_id,
                PendingRevengeRow.victim_id == victim_id,
            )
            .order_by(PendingRevengeRow.created_ts.asc())
            .limit(1)
        ).scalar_one_or_none()
        self.session.commit()
        return row

    def clear_pending_revenge(self, guild_id: str, victim_id: str, stolen_duck_id: str) -> None:
        self.session.execute(
            delete(PendingRevengeRow).where(
                PendingRevengeRow.guild_id == guild_id,
                PendingRevengeRow.victim_id == victim_id,
                PendingRevengeRow.stolen_duck_id == stolen_duck_id,
            )
        )
        self.session.commit()

    def duck_power(self, duck_row: DuckRow) -> int:
        return int(duck_row.attack) + int(duck_row.defense) + int(duck_row.speed)

    def handle_revenge_battle(self, guild_id: str, victim_id: str, pending: PendingRevengeRow) -> dict[str, Any]:
        thief_id = pending.thief_id
        stolen_duck_id = pending.stolen_duck_id

        if thief_id == victim_id:
            self.clear_pending_revenge(guild_id, victim_id, stolen_duck_id)
            return {"kind": "revenge_skip", "message": None}

        victim_fighter_id = self.get_random_stealable_duck_from_user(guild_id, victim_id)
        thief_fighter_id = self.get_random_stealable_duck_from_user(guild_id, thief_id)

        if not victim_fighter_id or not thief_fighter_id:
            self.clear_pending_revenge(guild_id, victim_id, stolen_duck_id)
            return {
                "kind": "revenge_closed",
                "message": "⚠️ Revenge window closed: one side has no stealable ducks left to battle with.",
            }

        victim_duck = self.get_duck(guild_id, victim_fighter_id)
        thief_duck = self.get_duck(guild_id, thief_fighter_id)
        if not victim_duck or not thief_duck:
            self.clear_pending_revenge(guild_id, victim_id, stolen_duck_id)
            return {
                "kind": "revenge_closed",
                "message": "⚠️ Revenge window closed due to missing duck data.",
            }

        victim_power = self.duck_power(victim_duck)
        thief_power = self.duck_power(thief_duck)
        margin = abs(victim_power - thief_power)

        if victim_power == thief_power:
            if random.random() < 0.5:
                victim_power += 1
            else:
                thief_power += 1
            margin = abs(victim_power - thief_power)

        victim_wins = victim_power > thief_power
        winner_name = victim_duck.name if victim_wins else thief_duck.name
        loser_name = thief_duck.name if victim_wins else victim_duck.name
        flavor = _random_battle_flavor(winner_name=winner_name, loser_name=loser_name, margin=margin)

        self.clear_pending_revenge(guild_id, victim_id, stolen_duck_id)

        results: list[str] = []
        stolen_duck = self.get_duck(guild_id, stolen_duck_id)
        stolen_is_with_thief = stolen_duck and stolen_duck.owner_id == thief_id

        if victim_wins and stolen_is_with_thief:
            self.remove_duck_from_user(guild_id, thief_id, stolen_duck_id)
            self.add_duck_to_user(guild_id, victim_id, stolen_duck_id)
            self.set_duck_owner(guild_id, stolen_duck_id, victim_id)
            results.append(f"🦆 <@{victim_id}> steals back **{stolen_duck.name}**!")

            if margin >= C.REVENGE_SWING_STEAL_THRESHOLD:
                bonus_id = self.get_random_stealable_duck_from_user(
                    guild_id, thief_id, exclude_duck_ids={stolen_duck_id}
                )
                if bonus_id:
                    bonus_duck = self.get_duck(guild_id, bonus_id)
                    if bonus_duck:
                        self.remove_duck_from_user(guild_id, thief_id, bonus_id)
                        self.add_duck_to_user(guild_id, victim_id, bonus_id)
                        self.set_duck_owner(guild_id, bonus_id, victim_id)
                        results.append(
                            f"💥 Massive victory (+{margin})! <@{victim_id}> also steals **{bonus_duck.name}**."
                        )
        elif victim_wins and not stolen_is_with_thief:
            results.append(
                f"⚠️ <@{victim_id}> wins the battle, but the original stolen duck is no longer with <@{thief_id}>."
            )
        else:
            results.append("😵 The thief defends successfully. Nothing is stolen back.")
            if margin >= C.REVENGE_SWING_STEAL_THRESHOLD:
                counter_id = self.get_random_stealable_duck_from_user(guild_id, victim_id)
                if counter_id:
                    counter_duck = self.get_duck(guild_id, counter_id)
                    self.remove_duck_from_user(guild_id, victim_id, counter_id)
                    self.add_duck_to_user(guild_id, thief_id, counter_id)
                    self.set_duck_owner(guild_id, counter_id, thief_id)
                    results.append(
                        f"🔥 Brutal defense (+{margin})! <@{thief_id}> steals **{counter_duck.name}** from <@{victim_id}>."
                    )

        description = (
            f"<@{victim_id}> sends **{victim_duck.name}** "
            f"(⚔️ {victim_duck.attack}  🛡️ {victim_duck.defense}  💨 {victim_duck.speed})\n"
            f"vs\n"
            f"<@{thief_id}> sends **{thief_duck.name}** "
            f"(⚔️ {thief_duck.attack}  🛡️ {thief_duck.defense}  💨 {thief_duck.speed})\n\n"
            f"{flavor}"
        )
        return {
            "kind": "revenge_battle",
            "victim_id": victim_id,
            "thief_id": thief_id,
            "victim_wins": victim_wins,
            "description": description,
            "outcome_lines": results,
            "footer": "This revenge trigger does not consume or refresh cooldowns.",
        }

    def _battle_hour_bucket_now(self) -> int:
        return utc_ts() // 3600

    @staticmethod
    def _seconds_until_next_utc_hour() -> int:
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return max(1, int((next_hour - now).total_seconds()))

    def can_battle(self, guild_id: str, user_id: str) -> tuple[bool, int | None]:
        """Whether the user may start or continue a !battle chain (UTC hour bucket + streak)."""
        b = self._battle_hour_bucket_now()
        row = self.get_user(guild_id, user_id)
        if row is None or row.battle_hour_bucket is None or row.battle_hour_bucket != b:
            return True, None
        if row.battle_streak:
            return True, None
        return False, self._seconds_until_next_utc_hour()

    def _set_battle_state(self, guild_id: str, user_id: str, bucket: int, streak: bool) -> None:
        row = self.get_user(guild_id, user_id)
        if row is None:
            return
        row.battle_hour_bucket = bucket
        row.battle_streak = streak
        self.session.commit()

    def duck_battle(self, user_id: str, guild_id: int) -> dict[str, Any]:
        """Random PvP battle: invoker's stealable duck vs another player's; winner takes loser's fighter."""
        gid = normalize_guild_id(guild_id)
        if self.zay.active(gid, user_id):
            return {
                "kind": "battle_zay_active",
                "message": "⚠️ Finish **Zay's ENERGY** with `!duck` before battling.",
            }

        allowed, wait_sec = self.can_battle(gid, user_id)
        if not allowed and wait_sec is not None:
            return {
                "kind": "battle_hourly_limit",
                "message": (
                    f"⏳ You've used your battle for this hour. "
                    f"Next window in **{_fmt_duration(wait_sec)}**."
                ),
                "seconds_until_next_hour": wait_sec,
            }

        challenger_duck_id = self.get_random_stealable_duck_from_user(gid, user_id)
        if not challenger_duck_id:
            return {
                "kind": "battle_no_duck",
                "message": "You need at least one **non-shiny, non-Legendary/Mythic** duck to battle with.",
            }

        opp = self.get_random_user_with_stealable_ducks(gid, exclude_user_id=user_id)
        if not opp:
            return {
                "kind": "battle_no_opponent",
                "message": "Nobody else in this server has a duck you can battle against (yet).",
            }

        opponent_id, opponent_duck_id = opp

        challenger_duck = self.get_duck(gid, challenger_duck_id)
        opponent_duck = self.get_duck(gid, opponent_duck_id)
        if not challenger_duck or not opponent_duck:
            return {"kind": "error", "message": "Battle aborted: missing duck data."}

        cp = self.duck_power(challenger_duck)
        op = self.duck_power(opponent_duck)
        margin = abs(cp - op)

        if cp == op:
            if random.random() < 0.5:
                cp += 1
            else:
                op += 1
            margin = abs(cp - op)

        challenger_wins = cp > op
        winner_name = challenger_duck.name if challenger_wins else opponent_duck.name
        loser_name = opponent_duck.name if challenger_wins else challenger_duck.name
        flavor = _random_battle_flavor(winner_name=winner_name, loser_name=loser_name, margin=margin)

        bucket = self._battle_hour_bucket_now()
        if challenger_wins:
            self.remove_duck_from_user(gid, opponent_id, opponent_duck_id)
            self.add_duck_to_user(gid, user_id, opponent_duck_id)
            self.set_duck_owner(gid, opponent_duck_id, user_id)
            self._set_battle_state(gid, user_id, bucket, True)
            outcome_lines = [
                f"🏆 <@{user_id}> wins! You take **{opponent_duck.name}** from <@{opponent_id}>.",
            ]
        else:
            self.remove_duck_from_user(gid, user_id, challenger_duck_id)
            self.add_duck_to_user(gid, opponent_id, challenger_duck_id)
            self.set_duck_owner(gid, challenger_duck_id, opponent_id)
            self._set_battle_state(gid, user_id, bucket, False)
            outcome_lines = [
                f"💔 <@{opponent_id}> wins! They take **{challenger_duck.name}** from you.",
            ]

        description = (
            f"<@{user_id}> sends **{challenger_duck.name}** "
            f"(⚔️ {challenger_duck.attack}  🛡️ {challenger_duck.defense}  💨 {challenger_duck.speed})\n"
            f"vs\n"
            f"<@{opponent_id}> sends **{opponent_duck.name}** "
            f"(⚔️ {opponent_duck.attack}  🛡️ {opponent_duck.defense}  💨 {opponent_duck.speed})\n\n"
            f"{flavor}"
        )

        footer = (
            "one battle per hour, win streaks keep you alive! — "
            "`!battle` again after a win, or wait for the next hour if you lost."
        )
        if challenger_wins:
            footer = "You won — `!battle` again now to keep rolling, or stop anytime. " + footer

        return {
            "kind": "battle",
            "challenger_wins": challenger_wins,
            "opponent_id": opponent_id,
            "description": description,
            "outcome_lines": outcome_lines,
            "footer": footer,
        }

    def _keish_batch_catch(self, gid: str, user_id: str) -> dict[str, Any]:
        """Create 3–7 new ducks and consume one Keish bonus roll. Caller must ensure rolls are pending."""
        n = random.randint(C.KEISH_BATCH_MIN, C.KEISH_BATCH_MAX)
        ducks_payload: list[dict[str, Any]] = []
        description_lines: list[str] = []
        react_crown = False

        for _ in range(n):
            url, ts = self.fetch_duck_url()
            nid = _generate_duck_id()
            duck_row = self.create_duck_record(gid, nid, url, ts)
            self.add_duck_to_user(gid, user_id, nid)
            self.set_duck_owner(gid, nid, user_id)
            shiny = bool(duck_row.shiny)
            if duck_row.rarity in ("Legendary", "Mythic"):
                react_crown = True
            ducks_payload.append(
                {
                    "id": duck_row.duck_id,
                    "name": duck_row.name,
                    "rarity": duck_row.rarity,
                    "attack": duck_row.attack,
                    "defense": duck_row.defense,
                    "speed": duck_row.speed,
                    "shiny": shiny,
                    "url": duck_row.url,
                }
            )
            shiny_note = " — Shiny" if shiny else ""
            description_lines.append(f"{duck_row.name} — {duck_row.rarity}{shiny_note}")

        self.keish.consume_one_roll(gid, user_id)

        keish_banner = random.choice(C.KEISH_SUCCESS_IMAGE_FILENAMES)
        duck_footer = "\n".join(description_lines)
        # Discord embed footers are capped at 2048 characters.
        if len(duck_footer) > 2048:
            duck_footer = duck_footer[:2045] + "..."

        return {
            "kind": "keish_catch",
            "title": C.KEISH_BATCH_CATCH_TITLE,
            "image_url": self.asset_url(keish_banner),
            "description": None,
            "color": 15844367,
            "footer": duck_footer,
            "react_crown": react_crown,
            "ducks": ducks_payload,
        }

    def duck_catch(
        self,
        user_id: str,
        guild_id: int | None,
        channel_id: int | None,
    ) -> dict[str, Any]:
        """Main !duck flow."""
        gid = normalize_guild_id(guild_id)
        try:
            pending = self.get_pending_revenge(gid, user_id)
            if pending:
                rev = self.handle_revenge_battle(gid, user_id, pending)
                if rev.get("kind") == "revenge_skip":
                    return self.duck_catch(user_id, guild_id, channel_id)
                return rev

            if self.zay.active(gid, user_id):
                effects = self.zay.handle_defense_attempt(gid, user_id)
                return {"kind": "zay_effects", "effects": effects}

            if self.keish.has_pending_rolls(gid, user_id):
                return self._keish_batch_catch(gid, user_id)

            on_cd, remaining = self.check_on_cooldown(gid, user_id)
            if on_cd:
                return {
                    "kind": "cooldown",
                    "remaining_seconds": remaining,
                    "message": f"⏳ You're still recovering! Next catch in **{_fmt_duration(remaining)}**.",
                }

            allow_zay_proc = gid != "0" and not self.zay.active(gid, user_id)
            allow_keish_proc = not self.zay.active(gid, user_id)
            outcome = _roll_duck_outcome(
                allow_zay_proc=bool(allow_zay_proc),
                allow_keish_proc=allow_keish_proc,
                allow_boot=True,
            )

            if outcome == "zay_proc":
                if guild_id is None or channel_id is None:
                    outcome = "new_duck"
                else:
                    self.zay.start(gid, user_id, guild_id, channel_id)
                    return {
                        "kind": "zay_proc",
                        "title": C.ZAY_PROC_TITLE,
                        "description": C.ZAY_PROC_DESCRIPTION,
                        "footer": C.ZAY_PROC_FOOTER,
                        "image_url": self.asset_url("zay_energy.gif"),
                    }

            if outcome == "keish_proc":
                self.keish.grant_rolls(gid, user_id)
                return {
                    "kind": "keish_proc",
                    "title": C.KEISH_PROC_TITLE,
                    "description": C.KEISH_PROC_DESCRIPTION,
                    "footer": C.KEISH_PROC_FOOTER,
                    "image_url": self.asset_url("keish_energy.gif"),
                }

            if outcome == "boot":
                return {
                    "kind": "boot",
                    "title": "Congrats on your new *duck*...?",
                    "description": (
                        f"{random.choice([
                            'You yank your line up triumphantly... and it\'s an old boot. The silence is deafening.',
                            'You pose like a champion angler, then realize you\'re holding a soggy boot.',
                            'You reel it in with confidence, only to discover pure footwear disappointment.',
                            'You expected feathers and glory; you got a boot and secondhand embarrassment.',
                            'You stare at the catch, then back at chat. Nobody needs to say anything.',
                        ])}\n\n"
                        "**Name:** Old Boot\n**Rarity:** Trash\n**Stats:** ⚔️ 0  🛡️ 0  💨 0\n"
                    ),
                    "color": 9936033,
                    "image_url": self.asset_url(random.choice(C.BOOT_IMAGE_FILENAMES)),
                    "footer": "No duck was actually caught. Try again.",
                }

            new_owner_id = user_id
            will_steal = outcome == "steal"
            steal_result = None
            if will_steal:
                steal_result = self.get_random_user_with_stealable_ducks(
                    gid, exclude_user_id=new_owner_id
                )

            theft_text = ""
            duck_row: DuckRow | None = None

            if will_steal and steal_result:
                prev_owner_id, duck_id = steal_result
                dr = self.get_duck(gid, duck_id)
                if dr:
                    self.remove_duck_from_user(gid, prev_owner_id, duck_id)
                    self.add_duck_to_user(gid, new_owner_id, duck_id)
                    self.set_duck_owner(gid, duck_id, new_owner_id)
                    self.refresh_user_cooldown(gid, prev_owner_id)
                    theft_text = (
                        f"\n⚠️ <@{prev_owner_id}> — your duck **{dr.name}** was stolen!\n"
                        "🌀 Your cooldown has been refreshed. Use `!duck` within **5 minutes** to trigger revenge."
                    )
                    self.set_pending_revenge(
                        guild_id=gid,
                        victim_id=prev_owner_id,
                        thief_id=new_owner_id,
                        stolen_duck_id=duck_id,
                    )
                    duck_row = dr

            if duck_row is None:
                url, ts = self.fetch_duck_url()
                nid = _generate_duck_id()
                duck_row = self.create_duck_record(gid, nid, url, ts)
                self.add_duck_to_user(gid, new_owner_id, nid)
                self.set_duck_owner(gid, nid, new_owner_id)

            cd = self.update_cooldown(gid, new_owner_id)
            cd_msg = (
                f"Your energy is preserved! Next catch available in: **{_fmt_duration(cd)}**."
                if cd <= C.COOLDOWN_MEAN
                else f"You're feeling a bit tired... Next catch available in: **{_fmt_duration(cd)}**."
            )

            rarity = duck_row.rarity
            name = duck_row.name
            shiny = bool(duck_row.shiny)
            atk, dfs, spd = duck_row.attack, duck_row.defense, duck_row.speed
            flair = C.RARITY_CATCH_FLAIR[rarity]

            title = (
                f"{'🌟✨ Shiny Duck Appeared! ✨🌟 ' if shiny else ''}"
                f"Congrats on your new duck!{' SO SHINY!' if shiny else ''}"
            )
            catch_body = (
                f"{flair}\n\n**Name:** {name}\n**Rarity:** {rarity}{' ✨' if shiny else ''}\n"
                f"**Stats:** ⚔️ {atk}  🛡️ {dfs}  💨 {spd}\n"
            )

            rarity_colors = {
                "Common": 10070709,
                "Uncommon": 3066993,
                "Rare": 3447003,
                "Legendary": 15105570,
                "Mythic": 10181046,
            }
            color = rarity_colors.get(rarity, 9936033)

            return {
                "kind": "catch",
                "title": title,
                "description": catch_body,
                "color": color,
                "image_url": duck_row.url,
                "footer": cd_msg,
                "theft_followup": theft_text or None,
                "react_crown": rarity in ("Legendary", "Mythic"),
                "duck": {
                    "id": duck_row.duck_id,
                    "name": duck_row.name,
                    "rarity": duck_row.rarity,
                    "attack": duck_row.attack,
                    "defense": duck_row.defense,
                    "speed": duck_row.speed,
                    "shiny": shiny,
                    "url": duck_row.url,
                },
            }
        except Exception as e:
            LOGGER.exception("duck_catch failed: %s", e)
            return {"kind": "error", "message": "Something went wrong catching your duck. Please try again in a moment."}

    def give_duck(self, guild_id: str, giver_id: str, receiver_id: str, duck_name: str) -> dict[str, Any]:
        """Transfer duck by name from giver's collection."""
        urow = self.get_user(guild_id, giver_id)
        if not urow or not urow.ducks_json:
            return {"ok": False, "error": "no_ducks", "message": "You don't have any ducks to give."}

        duck_ids = json.loads(urow.ducks_json)
        found: DuckRow | None = None
        for did in duck_ids:
            d = self.get_duck(guild_id, did)
            if d and d.name == duck_name:
                found = d
                break

        if not found:
            any_named = self.session.execute(
                select(DuckRow)
                .where(DuckRow.guild_id == guild_id, DuckRow.name == duck_name)
                .limit(1)
            ).scalar_one_or_none()
            if any_named:
                return {
                    "ok": False,
                    "error": "not_owned",
                    "message": f"You don't have a duck named **{duck_name}** to give.",
                }
            return {"ok": False, "error": "not_found", "message": f"No duck named **{duck_name}** was found."}

        self.remove_duck_from_user(guild_id, giver_id, found.duck_id)
        self.add_duck_to_user(guild_id, receiver_id, found.duck_id)
        self.set_duck_owner(guild_id, found.duck_id, receiver_id)
        return {
            "ok": True,
            "duck_name": found.name,
            "rarity": found.rarity,
            "receiver_id": receiver_id,
        }

    def release_duck(self, guild_id: str, user_id: str, duck_name: str) -> dict[str, Any]:
        """Remove and delete the user's most recently caught duck with this name (by ``timestamp``)."""
        want = duck_name.strip()
        if not want:
            return {"ok": False, "error": "bad_name", "message": "Provide a duck name."}

        urow = self.get_user(guild_id, user_id)
        if not urow or not urow.ducks_json:
            return {"ok": False, "error": "no_ducks", "message": "You don't have any ducks to release."}

        duck_ids = json.loads(urow.ducks_json)
        candidates: list[DuckRow] = []
        for did in duck_ids:
            d = self.get_duck(guild_id, did)
            if d and d.name == want:
                candidates.append(d)

        if not candidates:
            any_named = self.session.execute(
                select(DuckRow)
                .where(DuckRow.guild_id == guild_id, DuckRow.name == want)
                .limit(1)
            ).scalar_one_or_none()
            if any_named:
                return {
                    "ok": False,
                    "error": "not_owned",
                    "message": f"You don't have a duck named **{want}** to release.",
                }
            return {"ok": False, "error": "not_found", "message": f"No duck named **{want}** was found."}

        chosen = max(candidates, key=lambda d: int(d.timestamp))
        # Copy fields before obliterate: commit expires ORM state and the row is deleted.
        name_out = chosen.name
        rarity_out = chosen.rarity
        self.obliterate_duck(guild_id, user_id, chosen.duck_id)
        return {
            "ok": True,
            "duck_name": name_out,
            "rarity": rarity_out,
        }

    def leaderboard(self, guild_id: str) -> dict[str, Any]:
        rows = self.session.execute(select(UserRow).where(UserRow.guild_id == guild_id)).scalars().all()
        by_user: dict[str, UserRow] = {}
        leaderboard_list: list[tuple[str, int]] = []
        total_ducks = 0
        for row in rows:
            duck_ids = json.loads(row.ducks_json) if row.ducks_json else []
            c = len(duck_ids)
            total_ducks += c
            leaderboard_list.append((row.user_id, c))
            by_user[row.user_id] = row
        leaderboard_list.sort(key=lambda x: x[1], reverse=True)
        top10 = leaderboard_list[:10]
        top: list[dict[str, Any]] = []
        for uid, cnt in top10:
            urow = by_user.get(uid)
            display_name = (urow.global_name or urow.username) if urow else None
            top.append(
                {
                    "user_id": uid,
                    "count": cnt,
                    "display_name": display_name,
                    "username": urow.username if urow else None,
                    "avatar_hash": urow.avatar_hash if urow else None,
                }
            )
        return {"top": top, "total_ducks": total_ducks}

    def cache_user_identity(
        self,
        guild_id: str,
        user_id: str,
        *,
        username: str | None = None,
        global_name: str | None = None,
        avatar_hash: str | None = None,
    ) -> None:
        """Persist Discord identity fields on the user row so leaderboard/profile can render names."""
        if username is None and global_name is None and avatar_hash is None:
            return
        row = self.session.get(UserRow, (guild_id, user_id))
        if row is None:
            row = UserRow(
                guild_id=guild_id,
                user_id=user_id,
                ducks_json=json.dumps([]),
                last_catch=None,
                cooldown=None,
                keish_bonus_rolls=0,
                username=username,
                global_name=global_name,
                avatar_hash=avatar_hash,
            )
            self.session.add(row)
        else:
            changed = False
            if username is not None and row.username != username:
                row.username = username
                changed = True
            if global_name is not None and row.global_name != global_name:
                row.global_name = global_name
                changed = True
            if avatar_hash is not None and row.avatar_hash != avatar_hash:
                row.avatar_hash = avatar_hash
                changed = True
            if not changed:
                return
        self.session.commit()

    def enqueue_bot_announcement(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Queue a catch result so the Discord bot can post it in the channel."""
        row = BotAnnouncementRow(
            guild_id=int(guild_id),
            channel_id=int(channel_id),
            user_id=user_id,
            payload_json=json.dumps(payload),
            created_at=utc_ts(),
            delivered_at=None,
        )
        self.session.add(row)
        self.session.commit()

    def user_me_payload(self, guild_id: str, user_id: str) -> dict[str, Any]:
        """Shape for GET /me + clients."""
        row = self.get_user(guild_id, user_id)
        if not row:
            return {
                "userId": user_id,
                "ducks": [],
                "inventory": {"ducks": []},
                "duckCount": 0,
            }
        duck_ids = json.loads(row.ducks_json) if row.ducks_json else []
        ducks_out: list[dict[str, Any]] = []
        for did in duck_ids:
            d = self.get_duck(guild_id, did)
            if d:
                ducks_out.append(
                    {
                        "id": d.duck_id,
                        "duckId": d.duck_id,
                        "name": d.name,
                        "rarity": d.rarity,
                        "tier": d.rarity.lower(),
                        "attack": d.attack,
                        "defense": d.defense,
                        "speed": d.speed,
                        "shiny": d.shiny,
                        "url": d.url,
                        "timestamp": int(d.timestamp),
                    }
                )
        return {
            "userId": user_id,
            "ducks": ducks_out,
            "inventory": {"ducks": ducks_out},
            "duckCount": len(ducks_out),
        }
