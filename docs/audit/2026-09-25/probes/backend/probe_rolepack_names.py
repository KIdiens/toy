"""只读审计探针 4：角色包成员名的规范化/冲突行为（Windows 大小写不敏感）。

关注：校验用的 `role.json` 与**落盘**的 `role.json` 是否为同一份内容。
若存在大小写变体成员（role.json / Role.json），在大小写不敏感文件系统上
后写入者覆盖前者，而 `read_member("role.json")` 读到的仍是第一个成员 →
「校验对象 ≠ 落盘对象」。
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent / "toy"
sys.path.insert(0, str(REPO))
from host.roles import rolepack  # noqa: E402

WORK = Path(__file__).resolve().parent / "_probe_work2"
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

BASE = {
    "schema_version": 1, "role_id": 1, "name": "合法", "config_version": "1.0.0",
    "min_us": 1400, "max_us": 1600, "neutral_us": 1500,
    "axes": ["A", "B", "C", "D"], "actions": ["wave"],
}
EVIL = dict(BASE)
EVIL.update({"role_id": 2, "name": "冒名", "config_version": "6.6.6"})


def make_zip(entries: list[tuple[str, str]]) -> Path:
    p = WORK / "pack.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return p


print("=" * 78)
print("E1. 大小写变体 role.json / Role.json（大小写不敏感 FS）")
print("=" * 78)
pack = make_zip([
    ("role.json", json.dumps(BASE, ensure_ascii=False)),
    ("Role.json", json.dumps(EVIL, ensure_ascii=False)),
])
validated = json.loads(rolepack.read_member(pack, "role.json").decode("utf-8"))
print(f"    read_member('role.json') 读到的（=被 schema 校验的那份）：name={validated['name']!r} role_id={validated['role_id']}")
target = WORK / "roles" / "1"
rolepack.import_rolepack(pack, target)
landed = json.loads((target / "role.json").read_text(encoding="utf-8"))
print(f"    落盘 role.json 实际内容：name={landed['name']!r} role_id={landed['role_id']}")
same = landed == validated
print(f"    >>> 校验对象与落盘对象{'一致' if same else '不一致 → 校验可被绕过（契约安全）'}")

print()
print("=" * 78)
print("E2. 反斜杠成员名的落盘位置")
print("=" * 78)
pack2 = make_zip([
    ("role.json", json.dumps(BASE, ensure_ascii=False)),
    ("actions\\wave.json", "{}"),
])
target2 = WORK / "roles2" / "1"
rolepack.import_rolepack(pack2, target2)
for f in sorted(target2.rglob("*")):
    kind = "DIR " if f.is_dir() else "FILE"
    print(f"    {kind} {f.relative_to(target2)!s}  ->  is_file={f.is_file()}")

print()
print("=" * 78)
print("E3. 冒号成员名 role.json:evil（Windows ADS）")
print("=" * 78)
pack3 = make_zip([("role.json:evil", json.dumps(BASE, ensure_ascii=False))])
try:
    rolepack.validate_zip(pack3)
    print("    validate_zip 放行（冒号未在 forbidden/绝对路径判定内）")
except rolepack.RolePackError as exc:
    print(f"    validate_zip 拒绝：{exc}")
target3 = WORK / "roles3" / "1"
try:
    rolepack.import_rolepack(pack3, target3)
    for f in sorted(target3.rglob("*")):
        print(f"    落盘 {f.relative_to(target3)!s}  is_file={f.is_file()}  size={f.stat().st_size}")
    rj = target3 / "role.json"
    print(f"    role.json 是否存在(is_file) = {rj.is_file()}  （包内并无真实 role.json 成员）")
except rolepack.RolePackError as exc:
    print(f"    import_rolepack 拒绝：{exc}")

print("\n完成。")
