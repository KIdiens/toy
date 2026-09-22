"""档案仓储（只做存取，不含业务规则）。签名见 40-storage-design.md §6.1。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from host.storage.database import Database
from host.storage.errors import RecordNotFound, translate


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    display_name: str
    created_at: str


def create(db: Database, display_name: str, *, now: str) -> int:
    with db.transaction() as con:
        try:
            cur = con.execute(
                "INSERT INTO profiles (display_name, created_at) VALUES (?, ?)",
                (display_name, now),
            )
        except sqlite3.Error as exc:
            raise translate(exc) from exc
        return int(cur.lastrowid)


def get(db: Database, profile_id: int) -> Profile:
    row = db.connect().execute(
        "SELECT id, display_name, created_at FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise RecordNotFound(f"档案 {profile_id} 不存在")
    return Profile(row["id"], row["display_name"], row["created_at"])


def exists(db: Database, profile_id: int) -> bool:
    row = db.connect().execute(
        "SELECT 1 FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    return row is not None


def list_all(db: Database) -> list[Profile]:
    rows = db.connect().execute(
        "SELECT id, display_name, created_at FROM profiles ORDER BY id"
    ).fetchall()
    return [Profile(row["id"], row["display_name"], row["created_at"]) for row in rows]


def rename(db: Database, profile_id: int, display_name: str) -> None:
    with db.transaction() as con:
        cur = con.execute(
            "UPDATE profiles SET display_name = ? WHERE id = ?",
            (display_name, profile_id),
        )
        if cur.rowcount == 0:
            raise RecordNotFound(f"档案 {profile_id} 不存在")


def delete(db: Database, profile_id: int) -> bool:
    """硬删除；级联删 memories/reminders；幂等（无该行返回 False）。"""
    with db.transaction() as con:
        cur = con.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cur.rowcount > 0
