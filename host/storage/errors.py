"""存储层异常层级（**唯一定义处**，Lead 裁定 A1）。

本模块只 import 标准库，不 import 本项目任何上层模块，以满足「依赖只能向下」。
上层需要捕获时从 ``host.core.errors`` 导入（那里再导出），不得在本包外重复定义。
"""
from __future__ import annotations

import sqlite3


class StorageError(Exception):
    """存储层异常基类。"""


class MigrationError(StorageError):
    """迁移失败：携带版本号与根因。"""

    def __init__(self, version: int, cause: BaseException | None = None) -> None:
        self.version = version
        self.cause = cause
        detail = f"迁移 {version:04d} 失败"
        if cause is not None:
            detail = f"{detail}: {type(cause).__name__}: {cause}"
        super().__init__(detail)


class RecordNotFound(StorageError):
    """按主键查询无记录。"""


class UniqueViolation(StorageError):
    """UNIQUE / PRIMARY KEY 冲突。"""


class ForeignKeyViolation(StorageError):
    """外键约束失败（父行不存在）。"""


class ConstraintViolation(StorageError):
    """CHECK / NOT NULL 等其它约束失败。"""


_ERRONAME_MAP: dict[str, type[StorageError]] = {
    "SQLITE_CONSTRAINT_UNIQUE": UniqueViolation,
    "SQLITE_CONSTRAINT_PRIMARYKEY": UniqueViolation,
    "SQLITE_CONSTRAINT_FOREIGNKEY": ForeignKeyViolation,
    "SQLITE_CONSTRAINT_CHECK": ConstraintViolation,
    "SQLITE_CONSTRAINT_NOTNULL": ConstraintViolation,
}


def translate(exc: sqlite3.Error) -> StorageError:
    """按 ``sqlite_errorname`` 把 ``sqlite3.Error`` 映射为受控异常（比正则匹配消息更稳）。"""
    name = getattr(exc, "sqlite_errorname", "") or ""
    target = _ERRONAME_MAP.get(name)
    if target is not None:
        return target(str(exc))
    return StorageError(f"{type(exc).__name__}: {exc}")
