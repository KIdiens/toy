"""sqlite3 门面：连接与 PRAGMA、迁移运行器、事务、备份/还原。

**硬纪律**（40-storage-design.md §3.2）：``PRAGMA foreign_keys = ON`` 是连接级开关，
且在事务进行中执行会被**静默忽略**（读回仍是 0）。因此每条连接建立后，必须在
任何 DML **之前**完成 PRAGMA —— 这正是本模块 ``connect()`` 的唯一职责边界。
"""
from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

from host.storage.errors import MigrationError, translate

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent / "migrations"
BUSY_TIMEOUT_MS = 5000
_MIGRATION_NAME = re.compile(r"^(\d+)_")


def discover_migrations(directory: Path) -> list[tuple[int, Path]]:
    """扫描 ``migrations/NNNN_*.sql``，按版本号升序返回。"""
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found


def split_statements(text: str) -> list[str]:
    """基于 ``sqlite3.complete_statement`` 稳健切分（不误切注释里的分号）。"""
    statements: list[str] = []
    buffer = ""
    for line in text.splitlines(keepends=True):
        buffer += line
        if buffer.strip() and sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return statements


class Database:
    """线程局部连接 + 版本驱动的前向迁移。"""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        if not self._path.startswith(":"):
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    # ---------- 连接 ----------
    def connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is not None:
            return con
        con = sqlite3.connect(self._path, isolation_level=None)
        # 顺序不可调换：外键必须在任何 DML 之前开启。
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        con.row_factory = sqlite3.Row
        self._local.con = con
        with self._lock:
            self._connections.append(con)
        return con

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式 ``BEGIN IMMEDIATE`` … ``COMMIT``；异常 ``ROLLBACK`` 并**统一转成 StorageError**。

        统一转换保证存储层不向外泄漏裸 ``sqlite3.Error``（上层只需捕获 StorageError 家族）。
        """
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            # BEGIN 也会失败（写锁冲突 → `database is locked`，实测要等满 busy_timeout）。
            # 它必须在 try 内，否则裸 OperationalError 会绕过下面的 translate 直接冒到上层。
            raise translate(exc) from exc
        try:
            yield con
        except Exception as exc:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise translate(exc) from exc
            raise
        else:
            try:
                con.execute("COMMIT")
            except sqlite3.Error as exc:
                # COMMIT 失败必须回滚：否则连接会停在未知事务态，后续 BEGIN 继续报错。
                with contextlib.suppress(sqlite3.Error):
                    con.execute("ROLLBACK")
                raise translate(exc) from exc

    # ---------- 迁移 ----------
    @property
    def user_version(self) -> int:
        row = self.connect().execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def initialize(self) -> int:
        """建库并前向跑到最新版本，返回最终 ``user_version``。"""
        con = self.connect()
        current = self.user_version
        for version, path in discover_migrations(MIGRATIONS_DIR):
            if version <= current:
                continue
            apply_migration(con, version, path.read_text(encoding="utf-8"))
            current = version
        return current

    # ---------- 备份 / 还原 ----------
    def backup(self, dest: str | Path) -> None:
        target = sqlite3.connect(str(dest))
        try:
            self.connect().backup(target)
        finally:
            target.close()

    def restore(self, src: str | Path) -> None:
        """要求应用已静默（状态机停、无活跃连接）：先关连接再替换工作库。"""
        self.close_all()
        target = Path(self._path)
        for sidecar in (f"{self._path}-wal", f"{self._path}-shm"):
            with contextlib.suppress(FileNotFoundError):
                Path(sidecar).unlink()
        target.write_bytes(Path(src).read_bytes())

    # ---------- 关停 ----------
    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is None:
            return
        with contextlib.suppress(sqlite3.Error):
            con.close()
        self._local.con = None
        with self._lock:
            if con in self._connections:
                self._connections.remove(con)

    def close_all(self) -> None:
        with self._lock:
            connections, self._connections = self._connections, []
        for con in connections:
            with contextlib.suppress(sqlite3.Error):
                con.close()
        self._local = threading.local()


def apply_migration(con: sqlite3.Connection, version: int, sql_text: str) -> None:
    """单步事务执行一个迁移；任一步失败整步原子回滚。"""
    con.execute("BEGIN IMMEDIATE")
    try:
        for statement in split_statements(sql_text):
            con.execute(statement)
        con.execute(f"PRAGMA user_version = {int(version)}")
    except Exception as exc:  # noqa: BLE001  包裹为 MigrationError 并保留根因
        con.execute("ROLLBACK")
        if isinstance(exc, sqlite3.Error):
            raise MigrationError(version, translate(exc)) from exc
        raise MigrationError(version, exc) from exc
    else:
        con.execute("COMMIT")
