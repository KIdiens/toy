"""提醒仓储（只做存取，不含业务规则）。签名见 40-storage-design.md §6.3。

``claim_due`` 是幂等键的落地点：条件更新 ``WHERE status='pending'``，**只有真正
把 pending 改成 fired 的那一次返回 True**，从而保证重启/竞态下只触发一次（M8）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from host.storage.database import Database
from host.storage.errors import RecordNotFound, translate

PENDING: Final[str] = "pending"
FIRED: Final[str] = "fired"
CANCELLED: Final[str] = "cancelled"

_COLUMNS = (
    "id, profile_id, text, due_at_utc, timezone, repeat_rule, "
    "status, last_fired_at, idempotency_key"
)


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    profile_id: int | None
    text: str | None
    due_at_utc: str | None
    timezone: str | None
    repeat_rule: str | None
    status: str | None
    last_fired_at: str | None
    idempotency_key: str | None


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        profile_id=row["profile_id"],
        text=row["text"],
        due_at_utc=row["due_at_utc"],
        timezone=row["timezone"],
        repeat_rule=row["repeat_rule"],
        status=row["status"],
        last_fired_at=row["last_fired_at"],
        idempotency_key=row["idempotency_key"],
    )


def create(
    db: Database,
    *,
    text: str,
    due_at_utc: str,
    timezone: str,
    profile_id: int | None = None,
    repeat_rule: str | None = None,
    idempotency_key: str | None = None,
    status: str = PENDING,
    now: str,
) -> int:
    del now  # 表内无 created_at 列；保留形参以对齐签名契约
    with db.transaction() as con:
        try:
            cur = con.execute(
                "INSERT INTO reminders "
                "(profile_id, text, due_at_utc, timezone, repeat_rule, status, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile_id, text, due_at_utc, timezone, repeat_rule, status, idempotency_key),
            )
        except sqlite3.Error as exc:
            raise translate(exc) from exc
        return int(cur.lastrowid)


def get(db: Database, reminder_id: int) -> Reminder:
    row = db.connect().execute(
        f"SELECT {_COLUMNS} FROM reminders WHERE id = ?", (reminder_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFound(f"提醒 {reminder_id} 不存在")
    return _row_to_reminder(row)


def get_by_idempotency_key(db: Database, idempotency_key: str) -> Reminder | None:
    """按幂等键取回既有提醒（走唯一索引 ``reminders_idempotency_key_uq``）；无则 ``None``。

    存在的理由：「幂等冲突后要返回哪一条」必须有确定答案。调用方若靠
    ``list_by_profile(...)[-1]`` 之类的启发式去猜，重放请求会返回**另一条**提醒，
    属静默数据不一致。
    """
    row = db.connect().execute(
        f"SELECT {_COLUMNS} FROM reminders WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return _row_to_reminder(row) if row is not None else None


def list_by_profile(
    db: Database, profile_id: int, *, status: str | None = None
) -> list[Reminder]:
    sql = f"SELECT {_COLUMNS} FROM reminders WHERE profile_id = ?"
    params: list[object] = [profile_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id"
    rows = db.connect().execute(sql, params).fetchall()
    return [_row_to_reminder(row) for row in rows]


def list_due(db: Database, *, now_utc: str, status: str = PENDING) -> list[Reminder]:
    rows = db.connect().execute(
        f"SELECT {_COLUMNS} FROM reminders "
        "WHERE due_at_utc <= ? AND status = ? ORDER BY due_at_utc",
        (now_utc, status),
    ).fetchall()
    return [_row_to_reminder(row) for row in rows]


def claim_due(db: Database, reminder_id: int, *, fired_at: str) -> bool:
    with db.transaction() as con:
        cur = con.execute(
            "UPDATE reminders SET status = ?, last_fired_at = ? WHERE id = ? AND status = ?",
            (FIRED, fired_at, reminder_id, PENDING),
        )
        return cur.rowcount > 0


def cancel(db: Database, reminder_id: int) -> bool:
    with db.transaction() as con:
        cur = con.execute(
            "UPDATE reminders SET status = ? WHERE id = ? AND status = ?",
            (CANCELLED, reminder_id, PENDING),
        )
        return cur.rowcount > 0


def delete(db: Database, reminder_id: int) -> bool:
    with db.transaction() as con:
        cur = con.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return cur.rowcount > 0
