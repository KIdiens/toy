"""记忆仓储（只做存取，不含业务规则）。签名见 40-storage-design.md §6.2。

删除一律**硬删除**（Schema 无 ``deleted_at``），各自在**单步事务**内执行；
``delete`` 幂等（命中 0 行返回 ``False``，不抛 ``RecordNotFound``）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from host.storage.database import Database
from host.storage.errors import RecordNotFound, translate

_COLUMNS = "id, profile_id, kind, value, confirmed, created_at, expires_at"


@dataclass(frozen=True, slots=True)
class Memory:
    id: int
    profile_id: int
    kind: str
    value: str
    confirmed: bool
    created_at: str
    expires_at: str | None


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        profile_id=row["profile_id"],
        kind=row["kind"],
        value=row["value"],
        confirmed=bool(row["confirmed"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def add(
    db: Database,
    profile_id: int,
    kind: str,
    value: str,
    *,
    now: str,
    confirmed: bool = False,
    expires_at: str | None = None,
) -> int:
    with db.transaction() as con:
        try:
            cur = con.execute(
                "INSERT INTO memories "
                "(profile_id, kind, value, confirmed, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, kind, value, 1 if confirmed else 0, now, expires_at),
            )
        except sqlite3.Error as exc:
            raise translate(exc) from exc
        return int(cur.lastrowid)


def get(db: Database, memory_id: int) -> Memory:
    row = db.connect().execute(
        f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFound(f"记忆 {memory_id} 不存在")
    return _row_to_memory(row)


def list_by_profile(
    db: Database,
    profile_id: int,
    *,
    kind: str | None = None,
    include_expired: bool = False,
    now: str | None = None,
) -> list[Memory]:
    sql = f"SELECT {_COLUMNS} FROM memories WHERE profile_id = ?"
    params: list[object] = [profile_id]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    if now is not None and not include_expired:
        sql += " AND (expires_at IS NULL OR expires_at > ?)"
        params.append(now)
    sql += " ORDER BY id"
    rows = db.connect().execute(sql, params).fetchall()
    return [_row_to_memory(row) for row in rows]


def list_for_context(db: Database, profile_id: int, *, now: str) -> list[Memory]:
    """仅供记忆注入：只取 ``confirmed=1`` 且未过期。

    调用方**必须**把结果当不可信数据做数据化封装（40-storage-design.md §7.3）。
    """
    rows = db.connect().execute(
        f"SELECT {_COLUMNS} FROM memories "
        "WHERE profile_id = ? AND confirmed = 1 "
        "AND (expires_at IS NULL OR expires_at > ?) ORDER BY id",
        (profile_id, now),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def set_confirmed(db: Database, memory_id: int, confirmed: bool) -> None:
    with db.transaction() as con:
        cur = con.execute(
            "UPDATE memories SET confirmed = ? WHERE id = ?",
            (1 if confirmed else 0, memory_id),
        )
        if cur.rowcount == 0:
            raise RecordNotFound(f"记忆 {memory_id} 不存在")


def update_value(db: Database, memory_id: int, value: str) -> None:
    with db.transaction() as con:
        cur = con.execute("UPDATE memories SET value = ? WHERE id = ?", (value, memory_id))
        if cur.rowcount == 0:
            raise RecordNotFound(f"记忆 {memory_id} 不存在")


def delete(db: Database, memory_id: int) -> bool:
    with db.transaction() as con:
        cur = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def clear_profile(db: Database, profile_id: int) -> int:
    with db.transaction() as con:
        cur = con.execute("DELETE FROM memories WHERE profile_id = ?", (profile_id,))
        return cur.rowcount


def purge_expired(db: Database, *, now: str) -> int:
    with db.transaction() as con:
        cur = con.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        return cur.rowcount
