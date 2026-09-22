"""设置仓储：多行 KV（一行一键），只做读写。签名见 40-storage-design.md §6.4。

``value`` 一律存字符串（``"true"/"false"``、``"low"/"mid"/"high"``、``"zh"/"en"``）；
布尔与枚举的解析在业务层，存储层不做解释。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from host.storage.database import Database

KNOWN_KEYS: Final[tuple[str, ...]] = (
    "volume",
    "language",
    "child_mode_enabled",
    "night_mode_enabled",
    "memory_enabled",
    "mute",
)


def get(db: Database, key: str, *, default: str | None = None) -> str | None:
    row = db.connect().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    return row["value"] if row["value"] is not None else default


def get_all(db: Database) -> dict[str, str]:
    rows = db.connect().execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {row["key"]: row["value"] for row in rows if row["value"] is not None}


def set(db: Database, key: str, value: str, *, now: str) -> None:
    with db.transaction() as con:
        con.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, now),
        )


def set_many(db: Database, values: Mapping[str, str], *, now: str) -> None:
    """单事务原子写多行（要么全成，要么全不生效）。"""
    with db.transaction() as con:
        con.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            [(key, value, now) for key, value in values.items()],
        )


def delete(db: Database, key: str) -> bool:
    with db.transaction() as con:
        cur = con.execute("DELETE FROM settings WHERE key = ?", (key,))
        return cur.rowcount > 0
