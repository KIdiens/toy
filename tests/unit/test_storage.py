"""存储层：外键/级联、AC-12 持久化、AC-13 删除生效、幂等、迁移与备份还原。"""
from __future__ import annotations

import pytest
from host.core.clock import VirtualClock
from host.storage import (
    memories_repo,
    profiles_repo,
    reminders_repo,
    settings_repo,
)
from host.storage.database import MIGRATIONS_DIR, Database, discover_migrations
from host.storage.errors import (
    ConstraintViolation,
    ForeignKeyViolation,
    RecordNotFound,
    UniqueViolation,
)


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "toy.sqlite3")
    assert database.initialize() == 1
    yield database
    database.close_all()


@pytest.fixture()
def clock():
    return VirtualClock(start_iso="2026-01-01T00:00:00Z")


@pytest.fixture()
def profile(db, clock):
    return profiles_repo.create(db, "小明", now=clock.now_iso())


def test_migration_and_schema_landed(db):
    assert db.user_version == 1
    names = {row["name"] for row in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"profiles", "memories", "reminders", "settings"} <= names
    assert [version for version, _path in discover_migrations(MIGRATIONS_DIR)] == [1]


def test_foreign_keys_are_on_for_every_connection(db):
    assert db.connect().execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_orphan_insert_rejected(db, clock):
    with pytest.raises(ForeignKeyViolation):
        memories_repo.add(db, 4242, "interest", "无父行", now=clock.now_iso())


def test_confirmed_check_constraint(db, profile, clock):
    con = db.connect()
    with pytest.raises(ConstraintViolation), db.transaction():
        con.execute(
            "INSERT INTO memories (profile_id, kind, value, confirmed, created_at) "
            "VALUES (?, 'k', 'v', 2, ?)",
            (profile, clock.now_iso()),
        )


def test_cascade_delete_profile(db, profile, clock):
    memory_id = memories_repo.add(db, profile, "k", "v", now=clock.now_iso())
    reminders_repo.create(
        db, text="喝水", due_at_utc="2026-01-02T00:00:00Z", timezone="Asia/Shanghai",
        profile_id=profile, now=clock.now_iso(),
    )
    assert profiles_repo.delete(db, profile) is True
    with pytest.raises(RecordNotFound):
        memories_repo.get(db, memory_id)
    assert reminders_repo.list_by_profile(db, profile) == []


def test_memory_persists_across_connection_reopen(tmp_path, clock):  # AC-12
    path = tmp_path / "persist.sqlite3"
    first = Database(path)
    first.initialize()
    profile_id = profiles_repo.create(first, "小红", now=clock.now_iso())
    memory_id = memories_repo.add(
        first, profile_id, "interest", "喜欢恐龙", now=clock.now_iso(), confirmed=True
    )
    first.close()

    second = Database(path)
    assert second.user_version == 1
    memory = memories_repo.get(second, memory_id)
    assert memory.value == "喜欢恐龙"
    assert memory.confirmed is True
    second.close()


def test_deleted_memory_leaves_context(db, profile, clock):  # AC-13
    memory_id = memories_repo.add(
        db, profile, "interest", "喜欢恐龙", now=clock.now_iso(), confirmed=True
    )
    assert [m.id for m in memories_repo.list_for_context(
        db, profile, now=clock.now_iso())] == [memory_id]
    assert memories_repo.delete(db, memory_id) is True
    assert memories_repo.list_for_context(db, profile, now=clock.now_iso()) == []
    assert memories_repo.delete(db, memory_id) is False  # 幂等


def test_context_only_returns_confirmed_and_unexpired(db, profile, clock):
    memories_repo.add(db, profile, "a", "未确认", now=clock.now_iso())
    memories_repo.add(db, profile, "b", "已确认", now=clock.now_iso(), confirmed=True)
    memories_repo.add(
        db, profile, "c", "已过期", now=clock.now_iso(), confirmed=True,
        expires_at="2025-12-31T00:00:00Z",
    )
    values = [m.value for m in memories_repo.list_for_context(
        db, profile, now=clock.now_iso())]
    assert values == ["已确认"]


def test_purge_expired_and_clear_profile(db, profile, clock):
    memories_repo.add(
        db, profile, "a", "过期", now=clock.now_iso(),
        expires_at="2025-01-01T00:00:00Z",
    )
    memories_repo.add(db, profile, "b", "保留", now=clock.now_iso())
    assert memories_repo.purge_expired(db, now=clock.now_iso()) == 1
    assert memories_repo.clear_profile(db, profile) == 1
    assert memories_repo.list_by_profile(db, profile) == []


def test_memory_queries_and_updates(db, profile, clock):
    memory_id = memories_repo.add(db, profile, "k", "旧值", now=clock.now_iso())
    with pytest.raises(RecordNotFound):
        memories_repo.get(db, 9999)
    memories_repo.update_value(db, memory_id, "新值")
    memories_repo.set_confirmed(db, memory_id, True)
    memory = memories_repo.get(db, memory_id)
    assert (memory.value, memory.confirmed) == ("新值", True)
    with pytest.raises(RecordNotFound):
        memories_repo.update_value(db, 9999, "x")
    with pytest.raises(RecordNotFound):
        memories_repo.set_confirmed(db, 9999, True)
    assert memories_repo.list_by_profile(db, profile, kind="k")
    assert memories_repo.list_by_profile(db, profile, kind="其它") == []


def test_settings_round_trip_and_atomic_write(db, clock):  # AC-14 存储契约
    settings_repo.set(db, "memory_enabled", "false", now=clock.now_iso())
    assert settings_repo.get(db, "memory_enabled") == "false"
    assert settings_repo.get(db, "缺失", default="默认") == "默认"
    settings_repo.set_many(
        db, {"volume": "mid", "language": "zh"}, now=clock.now_iso()
    )
    assert settings_repo.get_all(db)["volume"] == "mid"
    assert settings_repo.set(db, "volume", "high", now=clock.now_iso()) is None
    assert settings_repo.get(db, "volume") == "high"
    assert settings_repo.delete(db, "volume") is True
    assert settings_repo.delete(db, "volume") is False


def test_reminders_lifecycle_and_idempotency(db, profile, clock):
    reminder_id = reminders_repo.create(
        db, text="喝水", due_at_utc="2026-01-01T00:00:00Z", timezone="Asia/Shanghai",
        profile_id=profile, idempotency_key="K1", now=clock.now_iso(),
    )
    assert [r.id for r in reminders_repo.list_due(
        db, now_utc="2026-01-02T00:00:00Z")] == [reminder_id]
    assert reminders_repo.claim_due(db, reminder_id, fired_at=clock.now_iso()) is True
    assert reminders_repo.claim_due(db, reminder_id, fired_at=clock.now_iso()) is False
    assert reminders_repo.list_due(db, now_utc="2026-01-02T00:00:00Z") == []
    with pytest.raises(UniqueViolation):
        reminders_repo.create(
            db, text="重复", due_at_utc="2026-01-01T00:00:00Z", timezone="Asia/Shanghai",
            profile_id=profile, idempotency_key="K1", now=clock.now_iso(),
        )


def test_reminder_cancel_and_delete(db, clock):
    reminder_id = reminders_repo.create(
        db, text="a", due_at_utc="2026-01-01T00:00:00Z", timezone="UTC", now=clock.now_iso()
    )
    assert reminders_repo.cancel(db, reminder_id) is True
    assert reminders_repo.cancel(db, reminder_id) is False
    assert reminders_repo.get(db, reminder_id).status == reminders_repo.CANCELLED
    assert reminders_repo.delete(db, reminder_id) is True
    assert reminders_repo.delete(db, reminder_id) is False
    with pytest.raises(RecordNotFound):
        reminders_repo.get(db, reminder_id)


def test_profile_rename_and_listing(db, clock):
    first = profiles_repo.create(db, "甲", now=clock.now_iso())
    second = profiles_repo.create(db, "乙", now=clock.now_iso())
    assert [p.id for p in profiles_repo.list_all(db)] == [first, second]
    profiles_repo.rename(db, first, "甲改名")
    assert profiles_repo.get(db, first).display_name == "甲改名"
    assert profiles_repo.exists(db, second) is True
    with pytest.raises(RecordNotFound):
        profiles_repo.rename(db, 9999, "x")
    with pytest.raises(RecordNotFound):
        profiles_repo.get(db, 9999)


def test_backup_and_restore_round_trip(tmp_path, clock):
    path = tmp_path / "work.sqlite3"
    db = Database(path)
    db.initialize()
    profile_id = profiles_repo.create(db, "小明", now=clock.now_iso())
    memories_repo.add(db, profile_id, "k", "v", now=clock.now_iso())

    backup_path = tmp_path / "backup.sqlite3"
    db.backup(backup_path)
    memories_repo.clear_profile(db, profile_id)
    assert memories_repo.list_by_profile(db, profile_id) == []

    db.restore(backup_path)
    assert len(memories_repo.list_by_profile(db, profile_id)) == 1
    db.close_all()
