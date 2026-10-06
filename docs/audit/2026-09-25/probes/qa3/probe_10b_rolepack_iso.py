"""Probe 10b: 角色包导入 —— 隔离沙箱（临时 root），绝不触碰真实仓库。

覆盖：压缩炸弹(真 DEFLATE)、原子性崩溃窗口、crash 残留导致启动 brick、合法导入正向路径。
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import REPO, TMP  # noqa: E402

from host.roles import rolepack  # noqa: E402
from host.roles.registry import RoleRegistry  # noqa: E402

ISO = TMP / "iso_rolepack"
if ISO.exists():
    shutil.rmtree(ISO, ignore_errors=True)
iso_roles = ISO / "content" / "roles"
iso_roles.mkdir(parents=True, exist_ok=True)
# 拷贝真实角色 1..3 到隔离区
for d in sorted((REPO / "content" / "roles").iterdir()):
    if d.is_dir():
        shutil.copytree(d, iso_roles / d.name)

print("隔离区角色:", sorted(p.name for p in iso_roles.iterdir()))


def make_zip(entries, *, deflate=True):
    buf = io.BytesIO()
    ct = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", ct) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


GOOD1 = json.loads((REPO / "content" / "roles" / "1" / "role.json").read_text(encoding="utf-8"))

# ---------- A) 真压缩炸弹 ----------
print("\n=== A) 压缩炸弹（真 DEFLATE，3MB 全零）===")
bomb = make_zip([("role.json", json.dumps(GOOD1).encode()),
                 ("bomb.bin", b"\x00" * (3 * 1024 * 1024))], deflate=True)
zp = TMP / "bomb.zip"; zp.write_bytes(bomb)
try:
    rolepack.validate_zip(zp)
    print("结果: 未拒绝（缺陷）")
except rolepack.RolePackError as exc:
    print("结果: 已拒绝 ->", exc)
except Exception as exc:  # noqa: BLE001
    print("结果: 其它异常 ->", type(exc).__name__, exc)

# 查看该 zip 的压缩比
with zipfile.ZipFile(zp) as z:
    for i in z.infolist():
        if not i.is_dir():
            print(f"   {i.filename}: size={i.file_size} comp={i.compress_size} "
                  f"ratio={i.file_size/max(1,i.compress_size):.0f}")

# ---------- B) 原子性崩溃窗口：第二次 rename 失败 ----------
print("\n=== B) 原子性：incoming.rename(target) 失败时旧版本是否保留 ===")
target = iso_roles / "1"
before_ver = json.loads((target / "role.json").read_text(encoding="utf-8"))["config_version"]
before_actions = sorted(p.name for p in (target / "actions").glob("*.json"))
print("导入前: config_version=", before_ver, "actions=", before_actions)

newpack = make_zip([("role.json", json.dumps({**GOOD1, "config_version": "9.9.9"}).encode()),
                    ("actions/nod.json", (target / "actions" / "nod.json").read_bytes())])
zp2 = TMP / "ok2.zip"; zp2.write_bytes(newpack)

import pathlib  # noqa: E402
orig_rename = pathlib.Path.rename


def failing_rename(self, dst):
    if self.name.endswith(".incoming"):
        raise OSError("[probe] 模拟第二次改名失败")
    return orig_rename(self, dst)


pathlib.Path.rename = failing_rename
try:
    rolepack.import_rolepack(zp2, target)
    print("结果: 竟然成功？")
except Exception as exc:  # noqa: BLE001
    print("结果: 抛异常 ->", type(exc).__name__, exc)
finally:
    pathlib.Path.rename = orig_rename

exists = target.exists()
print("导入后 target 仍存在?", exists)
if exists:
    ver_now = json.loads((target / "role.json").read_text(encoding="utf-8")).get("config_version")
    acts_now = sorted(p.name for p in (target / "actions").glob("*.json")) if (target / "actions").is_dir() else []
    print("  target config_version=", ver_now, "actions=", acts_now)
    print("  旧版本保留?", ver_now == before_ver and acts_now == before_actions)
else:
    print("  ❌ target 目录消失：旧版本丢失（违反 AC-25）")
print("  残留 .bak?", (iso_roles / "1.bak").exists(), " .incoming?", (iso_roles / "1.incoming").exists())

# ---------- C) crash 残留 *.bak 导致 load_all 失败 ----------
print("\n=== C) crash 残留（1.bak 与 1 配置文件版本不同）→ load_all 是否 brick ===")
target2 = iso_roles / "2"
bad_bak = iso_roles / "2.bak"
if bad_bak.exists():
    shutil.rmtree(bad_bak, ignore_errors=True)
bad_bak.mkdir(parents=True)
badrole = json.loads((target2 / "role.json").read_text(encoding="utf-8"))
badrole["config_version"] = "7.7.7"
(bad_bak / "role.json").write_text(json.dumps(badrole), encoding="utf-8")
reg = RoleRegistry(iso_roles)
try:
    reg.load_all()
    print("结果: load_all 成功（未受 .bak 影响）")
except Exception as exc:  # noqa: BLE001
    print("结果: load_all 抛异常 ->", type(exc).__name__, exc)

# ---------- D) 正向：合法包成功导入 ----------
print("\n=== D) 正向：合法包（config_version 升版）导入成功 ===")
good_new = make_zip([("role.json", json.dumps({**GOOD1, "config_version": "2.0.0"}).encode()),
                     ("actions/nod.json", (REPO / "content" / "roles" / "1" / "actions" / "nod.json").read_bytes())])
zp3 = TMP / "good3.zip"; zp3.write_bytes(good_new)
rep = rolepack.import_rolepack(zp3, iso_roles / "3")
print("导入 3 结果:", rep)
reg2 = RoleRegistry(iso_roles)
roles = reg2.load_all()
print("load_all:", [(r.role_id, r.config_version) for r in roles])
print("3 的 role.json:", json.loads((iso_roles / "3" / "role.json").read_text(encoding="utf-8"))["config_version"])
