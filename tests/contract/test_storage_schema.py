"""存储 schema / 迁移事务契约（40-storage-design.md §3、§4、§12 的回归化）。"""
from __future__ import annotations

import pytest
from host.storage.database import Database, apply_migration
from host.storage.errors import MigrationError


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "schema.sqlite3")
    database.initialize()
    yield database
    database.close_all()


def test_indexes_and_version(db):
    names = {row["name"] for row in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    assert {"memories_by_profile", "reminders_by_due",
            "reminders_idempotency_key_uq"} <= names
    assert db.user_version == 1


def test_user_version_participates_in_transaction(db):
    con = db.connect()
    con.execute("BEGIN IMMEDIATE")
    con.execute("PRAGMA user_version = 5")
    assert int(con.execute("PRAGMA user_version").fetchone()[0]) == 5
    con.execute("ROLLBACK")
    assert db.user_version == 1


def test_failed_migration_rolls_back_whole_step(db):
    con = db.connect()
    bad_sql = "CREATE TABLE good (id INTEGER);\nCREATE TABLE ;\n"
    with pytest.raises(MigrationError):
        apply_migration(con, 2, bad_sql)
    assert db.user_version == 1, "失败的迁移不得推进版本号"
    names = {row["name"] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "good" not in names, "失败迁移的第一句也必须被原子回滚"


def test_foreign_key_pragma_is_ignored_inside_transaction(db):
    """记录这条陷阱语义：事务中设 PRAGMA 会被静默忽略（因此必须连接级先设）。"""
    con = db.connect()
    con.execute("BEGIN IMMEDIATE")
    con.execute("PRAGMA foreign_keys = OFF")
    # 在事务内被忽略，读回仍是 1；这解释了为何 connect() 必须先行设置。
    assert int(con.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    con.execute("ROLLBACK")


def test_initialize_is_idempotent(db):
    assert db.initialize() == 1
    assert db.initialize() == 1
