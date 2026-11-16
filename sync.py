"""Sync users and PRO between the legacy bot DB and the new Mini App DB."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from config import DATA

logger = logging.getLogger("sync")
NEW_DB = Path(DATA) / "bot.db"
OLD_DB = Path(os.getenv("LEGACY_DB", r"C:\bots\voicebot\data\bot.db"))


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def sync_once() -> tuple[int, int]:
    if not OLD_DB.exists() or not NEW_DB.exists():
        return 0, 0
    old = _connect(OLD_DB)
    new = _connect(NEW_DB)
    to_new = 0
    to_old = 0
    now = int(time.time())
    try:
        new_ids = {row["id"] for row in new.execute("SELECT id FROM users")}
        old_by_tg = {
            row["tg_id"]: row
            for row in old.execute(
                "SELECT id,tg_id,username,is_premium,pro_until,created_at,last_seen_at FROM users"
            )
        }
        for tg_id, row in old_by_tg.items():
            if not tg_id:
                continue
            premium_until = now + 3650 * 86400 if row["is_premium"] else 0
            if tg_id in new_ids:
                new.execute(
                    """
                    UPDATE users SET
                        name=CASE WHEN name='' THEN ? ELSE name END,
                        pro_until=MAX(pro_until, ?)
                    WHERE id=?
                    """,
                    (row["username"] or "", premium_until, tg_id),
                )
            else:
                new.execute(
                    """
                    INSERT INTO users(id,name,balance,pro_until,created)
                    VALUES(?,?,?,?,?)
                    """,
                    (tg_id, row["username"] or "", 5, premium_until, now),
                )
                new_ids.add(tg_id)
                to_new += 1

        for row in new.execute("SELECT id,pro_until FROM users"):
            tg_id = row["id"]
            old_row = old_by_tg.get(tg_id)
            active = int(row["pro_until"] or 0) > now
            if old_row is None:
                old.execute(
                    """
                    INSERT INTO users(
                        tg_id,username,persona,created_at,last_seen_at,
                        is_premium,pro_until
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        tg_id,
                        "",
                        "soul",
                        _iso(now),
                        _iso(now),
                        1 if active else 0,
                        _iso(row["pro_until"]) if active else "",
                    ),
                )
                to_old += 1
            elif active:
                old.execute(
                    """
                    UPDATE users SET is_premium=1, pro_until=?
                    WHERE tg_id=?
                    """,
                    (_iso(row["pro_until"]), tg_id),
                )
        old.commit()
        new.commit()
        return to_new, to_old
    finally:
        old.close()
        new.close()


async def sync_loop() -> None:
    while True:
        try:
            added_new, added_old = await asyncio.to_thread(sync_once)
            if added_new or added_old:
                logger.info(
                    "users_synced",
                    extra={"new": added_new, "legacy": added_old},
                )
        except Exception:
            logger.exception("sync_failed")
        await asyncio.sleep(30)
