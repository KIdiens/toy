"""只读审计探针 3（重写）：存储契约与角色包安全。

- A. db.transaction() 的 `BEGIN IMMEDIATE` 不在 try 内 → 跨连接锁等待超时时，
     裸 sqlite3.OperationalError 逃逸（违反 docstring「统一转成 StorageError」）。
     触发方式：WAL + 两个线程两条连接，A 持写锁，B 的 BEGIN IMMEDIATE 等 busy_timeout 后失败。
- C. 幂等键重复时，调用方用 list_by_profile()[-1] 近似 → 返回的记录不是持该键的那条。
- D. 角色包成员名含反斜杠 / 冒号 / 大写时的校验与落盘行为。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import zipfile
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent / "toy"
sys.path.insert(0, str(REPO))

from host.storage import profiles_repo, reminders_repo  # noqa: E402
from host.storage.database import Database  # noqa: E402
from host.storage.errors import StorageError, UniqueViolation  # noqa: E402
from host.roles import rolepack  # noqa: E402

WORK = Path(__file__).resolve().parent / "_probe_work"
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- A
hr("A. transaction(): BEGIN IMMEDIATE 锁超时 → 裸 sqlite3.Error 逃逸？")
db = Database(WORK / "a.db")
db.initialize()  # 建 WAL 库

# 只留一条连接（当前线程）
holder_ready = threading.Event()
release = threading.Event()
result: dict[str, object] = {}


def holder() -> None:
    with db.transaction():
        holder_ready.set()
        release.wait(timeout=15)


t = threading.Thread(target=holder, daemon=True)
t.start()
holder_ready.wait(timeout=5)

start = time.monotonic()
try:
    with db.transaction():
        pass
except StorageError as exc:
    result["kind"] = "StorageError"
    result["detail"] = f"{type(exc).__name__}: {exc}"
except sqlite3.Error as exc:
    result["kind"] = "raw sqlite3.Error"
    result["detail"] = f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
finally:
    release.set()
    t.join(timeout=10)
elapsed = time.monotonic() - start

print(f"    第二个连接进入 transaction() 结果：{result.get('kind')}")
print(f"    详情：{result.get('detail')}")
print(f"    耗时 {elapsed:.1f}s（应≈ busy_timeout=5s）")
if result.get("kind") == "raw sqlite3.Error":
    print("    >>> 裸异常逃逸：违反 database.transaction docstring「统一转成 StorageError」")
elif result.get("kind") == "StorageError":
    print("    >>> 已被 translate，未见逃逸（证伪该担忧）")
else:
    print("    >>> 未复现（可能未真正争锁）")

# ---------------------------------------------------------------- C
hr("C. 幂等键重复 → 调用方返回的记录是否正确")
db2 = Database(WORK / "c.db")
db2.initialize()
pid = profiles_repo.create(db2, "宝宝", now="2026-01-01T00:00:00Z")
a = reminders_repo.create(db2, text="A-喝水", due_at_utc="2026-01-01T08:00:00Z",
                          timezone="Asia/Shanghai", profile_id=pid,
                          idempotency_key="KEY-A", now="2026-01-01T00:00:00Z")
b = reminders_repo.create(db2, text="B-睡觉", due_at_utc="2026-01-01T09:00:00Z",
                          timezone="Asia/Shanghai", profile_id=pid,
                          idempotency_key="KEY-B", now="2026-01-01T00:00:00Z")
print(f"    已建：A(id={a}, KEY-A)、B(id={b}, KEY-B)")
try:
    reminders_repo.create(db2, text="A-喝水", due_at_utc="2026-01-01T08:00:00Z",
                          timezone="Asia/Shanghai", profile_id=pid,
                          idempotency_key="KEY-A", now="2026-01-01T00:00:00Z")
    print("    重放 KEY-A：未冲突（意外）")
except UniqueViolation:
    # 复刻 services.create_reminder 的幂等分支（services.py:86-88）
    picked = reminders_repo.list_by_profile(db2, pid)[-1]
    ok = picked.id == a
    print("    重放 KEY-A → UniqueViolation（符合预期）")
    print(f"    调用方 API list_by_profile(pid)[-1] 取回 = id={picked.id} "
          f"text={picked.text!r} key={picked.idempotency_key!r}")
    print(f"    期望取回                              = id={a} text={'A-喝水'!r} key={'KEY-A'!r}")
    print(f"    >>> 返回记录{'正确' if ok else '错误 → 数据完整性缺陷'}")

# ---------------------------------------------------------------- D
hr("D. 角色包成员名：反斜杠 / 冒号 / 大写")
VALID_ROLE = {
    "schema_version": 1, "role_id": 1, "name": "小星", "config_version": "9.9.9",
    "min_us": 1400, "max_us": 1600, "neutral_us": 1500,
    "axes": ["左臂", "右臂", "头部左右", "腰部左右"], "actions": ["wave"],
}


def make_zip(name: str, entries: dict[str, str]) -> Path:
    p = WORK / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return p


for idx, member in enumerate(("actions\\wave.json", "actions:evil.json", "ROLE.JSON")):
    pack = make_zip(f"pack{idx}.zip",
                    {"role.json": json.dumps(VALID_ROLE, ensure_ascii=False), member: "{}"})
    try:
        rolepack.validate_zip(pack)
    except rolepack.RolePackError as exc:
        print(f"    成员名 {member!r}: validate_zip 拒绝（{exc}）")
        continue
    target = WORK / f"roles{idx}" / "1"
    rolepack.import_rolepack(pack, target)
    landed = sorted(str(f.relative_to(target)) for f in target.rglob("*") if f.is_file())
    print(f"    成员名 {member!r}: validate_zip 放行；落盘 = {landed}")

print("\n完成。")
