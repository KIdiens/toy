"""存储层锁冲突的判别与翻译（第 7 组 D3）。

对应实现在 ``host/storage/errors.py`` 的 ``is_busy`` / ``translate``。

**为什么单独测判别函数**：``translate`` 对锁冲突的处理与其它错误**分道扬镳**
（StorageBusy vs 通用 StorageError），而这个分岔一旦被改坏，web 层就会把
可重试的锁冲突回成 500 —— 且**不会有任何测试立刻变红**（信封仍然合法），
只会静静退化成「等满 busy_timeout 再回 500」的老行为。所以必须钉住判别本身。
"""
from __future__ import annotations

import sqlite3

import pytest
from host.storage.errors import (
    ConstraintViolation,
    ForeignKeyViolation,
    RecordNotFound,
    StorageBusy,
    StorageError,
    UniqueViolation,
    is_busy,
    translate,
)


class _FakeOperationalError(sqlite3.OperationalError):
    """带``sqlite_errorname`` 的 OperationalError。

    ``sqlite3`` 只在**真实** sqlite 调用失败时填充这两个属性，无法在单测里
    构造出 ``SQLITE_BUSY_SNAPSHOT`` 这类扩展码（需要真实的快照冲突场景）。
    这里用子类注入，正是为了覆盖那些真实路径**难以复现**的扩展码 ——
    它们恰恰是「只认等值匹配」会漏判的那一批。
    """

    def __init__(self, message: str, errorname: str, errorcode: int) -> None:
        super().__init__(message)
        self.sqlite_errorname = errorname
        self.sqlite_errorcode = errorcode


@pytest.mark.parametrize("errorname,errorcode", [
    ("SQLITE_BUSY", 5),
    # 扩展码：基础码同属 BUSY，但 errorname 带后缀。只认等值会漏判这几类。
    ("SQLITE_BUSY_SNAPSHOT", 517),
    ("SQLITE_BUSY_TIMEOUT", 773),
    ("SQLITE_LOCKED", 6),
    ("SQLITE_LOCKED_SHAREDCACHE", 262),
])
def test_is_busy_accepts_busy_and_extended_codes(errorname: str, errorcode: int) -> None:
    exc = _FakeOperationalError("database is locked", errorname, errorcode)
    assert is_busy(exc) is True
    assert isinstance(translate(exc), StorageBusy)


@pytest.mark.parametrize("errorname,errorcode", [
    ("SQLITE_ERROR", 1),          # 表不存在 / 语法错
    ("SQLITE_READONLY", 8),
    ("SQLITE_CORRUPT", 11),
    ("SQLITE_CANTOPEN", 14),
    ("SQLITE_CONSTRAINT_UNIQUE", 2067),
    ("SQLITE_CONSTRAINT_FOREIGNKEY", 787),
])
def test_is_busy_rejects_deterministic_failures(errorname: str, errorcode: int) -> None:
    """确定性故障**不得**被判成锁冲突，否则会把 500 错误升级成误导性的 503。"""
    exc = _FakeOperationalError("no such table: reminders", errorname, errorcode)
    assert is_busy(exc) is False
    assert not isinstance(translate(exc), StorageBusy)


def test_translate_preserves_constraint_mapping() -> None:
    """加busy 判别**不得**破坏既有的约束异常映射（回归护栏）。"""
    cases = {
        "SQLITE_CONSTRAINT_UNIQUE": UniqueViolation,
        "SQLITE_CONSTRAINT_PRIMARYKEY": UniqueViolation,
        "SQLITE_CONSTRAINT_FOREIGNKEY": ForeignKeyViolation,
        "SQLITE_CONSTRAINT_CHECK": ConstraintViolation,
        "SQLITE_CONSTRAINT_NOTNULL": ConstraintViolation,
    }
    for errorname, expected in cases.items():
        exc = _FakeOperationalError("constraint failed", errorname, 19)
        assert type(translate(exc)) is expected, errorname


def test_storage_busy_is_a_storage_error_subclass() -> None:
    """StorageBusy 必须是 StorageError 子类。

    两条纪律同时成立：① 上层「捕获 StorageError 家族」的既有代码不必改；
    ② web 层注册更具体的子类处理器才生效（MRO 优先）。
    """
    assert issubclass(StorageBusy, StorageError)
    exc = _FakeOperationalError("database is locked", "SQLITE_BUSY", 5)
    assert isinstance(translate(exc), StorageError)


def test_is_busy_tolerates_missing_attributes() -> None:
    """没有 errorname 属性的异常（如手工构造的）必须安全返回 False，不得抛异常。

    判别函数在异常翻译**路径**上，任何二次异常都会把原始故障变成更难查的故障。
    """
    assert is_busy(sqlite3.OperationalError("boom")) is False
    bare = sqlite3.OperationalError("boom")
    bare.sqlite_errorname = None
    assert is_busy(bare) is False


def test_record_not_found_is_not_busy() -> None:
    """RecordNotFound 与锁无关：web 层靠它回 404，别被误判。"""
    assert not issubclass(RecordNotFound, StorageBusy)