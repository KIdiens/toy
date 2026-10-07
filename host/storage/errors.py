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


class StorageBusy(StorageError):
    """存储层**临时性资源竞争**：写锁被别的连接占着（SQLITE_BUSY / SQLITE_LOCKED）。

    与「磁盘坏了」「表不存在」这类**确定性**故障必须分开：前者在对方提交后重试即成功，
    调用方应立即可重试；后者重试多少次都一样。对外映射 503 + ``Retry-After``。
    """


_ERRONAME_MAP: dict[str, type[StorageError]] = {
    "SQLITE_CONSTRAINT_UNIQUE": UniqueViolation,
    "SQLITE_CONSTRAINT_PRIMARYKEY": UniqueViolation,
    "SQLITE_CONSTRAINT_FOREIGNKEY": ForeignKeyViolation,
    "SQLITE_CONSTRAINT_CHECK": ConstraintViolation,
    "SQLITE_CONSTRAINT_NOTNULL": ConstraintViolation,
}

# 锁冲突的 errorname **前缀**集合。必须用前缀而非精确等值：扩展码会带自己的名字，
# 实测 SQLITE_BUSY_SNAPSHOT=517 / SQLITE_BUSY_TIMEOUT=773 / SQLITE_LOCKED_SHAREDCACHE=262
# 都以``SQLITE_BUSY`` / ``SQLITE_LOCKED`` 开头，只认等值会把「快照冲突」这类
# 同样可重试的锁冲突漏判成普通 StorageError（→ 500），重试语义就丢了。
_BUSY_PREFIXES = ("SQLITE_BUSY", "SQLITE_LOCKED")


def is_busy(exc: sqlite3.Error) -> bool:
    """判定一个 ``sqlite3.Error`` 是否为**锁冲突**（可重试的临时资源竞争）。

    为什么不匹配错误消息字符串（``"database is locked" in str(exc)``）：

    1. **不稳定**：消息文案随SQLite 版本与本地化变化，不是 API 契约；
    2. **不完整**：扩展码（``SQLITE_BUSY_SNAPSHOT`` 等）的消息与主码不同，
       按主码文案匹配会漏判；
    3. **粒度过粗**：``sqlite_errorname`` 是``sqlite3`` 暴露的**结构化**错误名，
       等价于 C 层 ``sqlite3_errstr`` 的分类，本项目``translate`` 已在用它做约束映射
       （见 :data:`_ERRONAME_MAP`），锁冲突沿用同一判据可保持一致、不引入第二套机制。

    之所以用**前缀**而非 ``== "SQLITE_BUSY"``：见 :data:`_BUSY_PREFIXES` 的说明。
    """
    name = getattr(exc, "sqlite_errorname", "") or ""
    return name.startswith(_BUSY_PREFIXES)


def translate(exc: sqlite3.Error) -> StorageError:
    """按 ``sqlite_errorname`` 把 ``sqlite3.Error`` 映射为受控异常（比正则匹配消息更稳）。"""
    name = getattr(exc, "sqlite_errorname", "") or ""
    if is_busy(exc):
        return StorageBusy(f"{type(exc).__name__}: {exc}")
    target = _ERRONAME_MAP.get(name)
    if target is not None:
        return target(str(exc))
    return StorageError(f"{type(exc).__name__}: {exc}")
