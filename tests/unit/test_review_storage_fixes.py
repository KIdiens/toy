"""复查整改（第一批）回归护栏：存储契约与异常翻译。

覆盖三条实测可达的 500（无统一信封）与一条裸异常泄漏：
- 幂等重放取回**另一条**提醒（`list_by_profile(...)[-1]` 冒充既有记录）
- 档案/提醒编号越出 SQLite 64 位整数范围 → 裸 `OverflowError`
- 非法 `due_at_utc` → 裸 `ValueError`
- 写锁冲突时 `BEGIN IMMEDIATE` 在 try 之外 → 裸 `sqlite3.OperationalError`
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from host.core.errors import ClarifyError
from host.storage.database import Database
from host.storage.errors import StorageError

TOO_BIG = "9" * 19  # ≈1e19 > 2**63-1，实测即可在驱动层触发 OverflowError


# ------------------------------------------------------------------ 幂等
def test_idempotent_replay_returns_the_same_reminder(sim_context) -> None:
    base = {"text": "该睡觉了", "due_at_utc": "2030-01-01T00:00:00Z", "profile_id": "default"}
    first = sim_context.create_reminder({**base, "idempotency_key": "KEY-A"})
    other = sim_context.create_reminder(
        {**base, "text": "该喝水了", "idempotency_key": "KEY-B"})

    replay = sim_context.create_reminder({**base, "idempotency_key": "KEY-A"})

    assert other["id"] != first["id"], "前置条件未构造成功：两条记录应当不同"
    assert replay["id"] == first["id"], "幂等重放返回了另一条记录"
    assert replay["text"] == first["text"]


def test_empty_idempotency_key_is_treated_as_absent(sim_context) -> None:
    """空串幂等键必须归一成「无键」。

    DB 的唯一索引把 `""` 当**真实取值**，而调用方若把它当「无键」，第二次同空键提交
    就会撞唯一约束、又找不到既有记录 → 抛 HostError → 纯文本 500。
    用户可控输入不该产生 500。
    """
    payload = {"text": "喝水", "due_at_utc": "2030-01-01T00:00:00Z",
               "profile_id": "default", "idempotency_key": ""}

    first = sim_context.create_reminder(dict(payload))
    second = sim_context.create_reminder(dict(payload))

    assert first["id"] and second["id"]


# ------------------------------------------------------------------ 编号范围
@pytest.mark.parametrize("bad", [TOO_BIG, str(2**63), f"-{2**63 + 1}"])
def test_profile_id_out_of_range_is_a_clarify_error(sim_context, bad: str) -> None:
    with pytest.raises(ClarifyError):
        sim_context.list_memories(profile_id=bad)


def test_reminder_id_out_of_range_is_a_clarify_error(sim_context) -> None:
    with pytest.raises(ClarifyError):
        sim_context.cancel_reminder(TOO_BIG)


def test_non_numeric_profile_id_is_a_clarify_error(sim_context) -> None:
    with pytest.raises(ClarifyError):
        sim_context.list_memories(profile_id="not-a-number")


# ------------------------------------------------------------------ 时间格式
def test_invalid_due_at_is_a_clarify_error(sim_context) -> None:
    with pytest.raises(ClarifyError):
        sim_context.create_reminder(
            {"text": "喝水", "due_at_utc": "not-a-date", "profile_id": "default"})


# ------------------------------------------------------------------ 事务异常翻译
def test_write_lock_conflict_raises_storage_error_not_raw_sqlite(tmp_path: Path) -> None:
    """`BEGIN IMMEDIATE` 失败必须 translate 成 StorageError，不得漏裸 sqlite3 异常。"""
    db_path = tmp_path / "lock.sqlite3"
    db = Database(db_path)
    db.initialize()
    db.connect().execute("PRAGMA busy_timeout = 120")  # 别让用例等满默认 5s

    blocker = sqlite3.connect(str(db_path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # 占住写锁
    try:
        with pytest.raises(StorageError), db.transaction() as con:
            con.execute("SELECT 1")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        db.close_all()


def test_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    """修复不得破坏事务本身：成功要提交、异常要回滚。"""
    db = Database(tmp_path / "tx.sqlite3")
    db.initialize()
    try:
        with db.transaction() as con:
            con.execute("CREATE TABLE probe (id INTEGER)")
            con.execute("INSERT INTO probe (id) VALUES (1)")

        with pytest.raises(RuntimeError), db.transaction() as con:
            con.execute("INSERT INTO probe (id) VALUES (2)")
            raise RuntimeError("故意失败")

        rows = db.connect().execute("SELECT id FROM probe ORDER BY id").fetchall()
        assert [r[0] for r in rows] == [1], "提交/回滚语义被破坏"
    finally:
        db.close_all()
