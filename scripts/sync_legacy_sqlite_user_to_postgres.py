#!/usr/bin/env python3
"""Copy one user's flock from legacy SQLite (no guild_id) into Postgres for a target guild.

Legacy schema: users(user_id PK), ducks(duck_id PK).
Postgres: users(guild_id, user_id), ducks(guild_id, duck_id).

Usage (from Duck-Game-backend with .venv):
  .venv/bin/python scripts/sync_legacy_sqlite_user_to_postgres.py \\
    --sqlite /path/to/ducks.db \\
    --guild-id 1139448045843521557 \\
    --user-id 171463772207185920
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from duck_game_backend.config import get_settings
from duck_game_backend.models import DuckRow, UserRow


def _bool_from_sqlite(v: int | bool) -> bool:
    if isinstance(v, bool):
        return v
    return bool(v)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True, help="Path to legacy ducks.db")
    parser.add_argument("--guild-id", type=str, required=True, help="Discord guild snowflake string")
    parser.add_argument("--user-id", type=str, required=True, help="Discord user snowflake string")
    args = parser.parse_args()

    if not args.sqlite.is_file():
        raise SystemExit(f"SQLite file not found: {args.sqlite}")

    guild_id = str(args.guild_id).strip()
    user_id = str(args.user_id).strip()

    ls = sqlite3.connect(args.sqlite)
    ls.row_factory = sqlite3.Row
    urow = ls.execute(
        "SELECT user_id, ducks_json, last_catch, cooldown FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if urow is None:
        raise SystemExit(f"No user row in SQLite for user_id={user_id!r}")

    duck_ids: list[str] = json.loads(urow["ducks_json"])
    if not isinstance(duck_ids, list):
        raise SystemExit("legacy ducks_json is not a JSON array")

    ducks_payload: list[sqlite3.Row] = []
    missing: list[str] = []
    for did in duck_ids:
        d = ls.execute("SELECT * FROM ducks WHERE duck_id = ?", (did,)).fetchone()
        if d is None:
            missing.append(did)
            continue
        ducks_payload.append(d)

    if missing:
        print(f"warning: {len(missing)} duck ids in ducks_json have no row in legacy ducks table (skipped)")

    settings = get_settings()
    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        existing = session.get(UserRow, (guild_id, user_id))
        if existing is None:
            session.add(
                UserRow(
                    guild_id=guild_id,
                    user_id=user_id,
                    ducks_json=json.dumps(duck_ids),
                    last_catch=urow["last_catch"],
                    cooldown=urow["cooldown"],
                )
            )
        else:
            existing.ducks_json = json.dumps(duck_ids)
            existing.last_catch = urow["last_catch"]
            existing.cooldown = urow["cooldown"]

        valid_ids = [d["duck_id"] for d in ducks_payload]
        for d in ducks_payload:
            row = session.get(DuckRow, (guild_id, d["duck_id"]))
            shiny = _bool_from_sqlite(d["shiny"])
            if row is None:
                session.add(
                    DuckRow(
                        guild_id=guild_id,
                        duck_id=d["duck_id"],
                        url=d["url"],
                        rarity=d["rarity"],
                        name=d["name"],
                        attack=int(d["attack"]),
                        defense=int(d["defense"]),
                        speed=int(d["speed"]),
                        shiny=shiny,
                        timestamp=int(d["timestamp"]),
                        owner_id=user_id,
                    )
                )
            else:
                row.url = d["url"]
                row.rarity = d["rarity"]
                row.name = d["name"]
                row.attack = int(d["attack"])
                row.defense = int(d["defense"])
                row.speed = int(d["speed"])
                row.shiny = shiny
                row.timestamp = int(d["timestamp"])
                row.owner_id = user_id

        # Clear owner on guild ducks that were tied to this user but are no longer in inventory.
        stmt = select(DuckRow).where(
            DuckRow.guild_id == guild_id,
            DuckRow.owner_id == user_id,
        )
        if valid_ids:
            stmt = stmt.where(DuckRow.duck_id.notin_(valid_ids))
        orphan = list(session.execute(stmt).scalars().all())
        for o in orphan:
            o.owner_id = None

        session.commit()

    print(
        f"ok: guild={guild_id} user={user_id} — "
        f"ducks_json={len(duck_ids)} ids, upserted {len(ducks_payload)} duck rows, "
        f"cleared owner on {len(orphan)} orphan rows"
    )


if __name__ == "__main__":
    main()
