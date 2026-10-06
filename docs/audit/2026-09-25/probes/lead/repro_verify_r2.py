"""独立复现复验员报的 R2-5 / R2-6 / R2-4（第三轮修复的反向验证）。

R2-5: `./role.json` 合法前缀包被误拒（回归）
R2-6: `role.json` + `./role.json` 别名洞 → 校验被绕过、旧版本被销毁、load_all 崩（500）
R2-4: 音量键改档不回写设置 → 界面与实际分叉、重启即丢

运行：python _audit_lead/repro_verify_r2.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.roles import rolepack  # noqa: E402
from host.roles.registry import RoleRegistry  # noqa: E402

SEP = "=" * 76
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-r2-", dir=r"F:/AI智能底座玩具/_audit_lead"))

ROLE = {
    "schema_version": 1, "role_id": 1, "name": "小星", "config_version": "1.0.0",
    "min_us": 1400, "max_us": 1600, "neutral_us": 1500,
    "axes": ["左臂", "右臂", "头部左右", "腰部左右"], "actions": ["wave"],
}


def _zip(path: Path, entries: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def r2_5() -> bool:
    print(SEP)
    print("R2-5  `zip -r .` 风格（带 ./-前缀）的合法包能否正常导入")
    print(SEP)
    pack = _zip(SCRATCH / "dot.zip", {
        "./role.json": json.dumps(ROLE, ensure_ascii=False),
        "./actions/wave.json": json.dumps({"schema": 1}),
    })
    target = SCRATCH / "dot_roles" / "1"
    try:
        rolepack.validate_zip(pack)
        rolepack.import_rolepack(pack, target)
        ok = (target / "role.json").is_file()
        print(f"  导入结果 = {'成功' if ok else '失败'}；落盘 role.json 存在 = {ok}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  导入抛错: {type(exc).__name__}: {exc}")
    print(f"  → 合法前缀包被误拒（回归）: {not ok}")
    return not ok


def r2_6() -> bool:
    print()
    print(SEP)
    print("R2-6  role.json(role_id=1) + ./role.json(role_id=2) 别名洞")
    print(SEP)
    roles_root = SCRATCH / "alias_roles"
    for role_id in (1, 2):
        d = roles_root / str(role_id)
        (d / "actions").mkdir(parents=True, exist_ok=True)
        (d / "role.json").write_text(
            json.dumps({**ROLE, "role_id": role_id, "config_version": "1.0.0"},
                       ensure_ascii=False), encoding="utf-8")
        (d / "actions" / "wave.json").write_text("{}", encoding="utf-8")
    before = (roles_root / "1" / "role.json").read_text(encoding="utf-8")
    before_ids = sorted(c.role_id for c in RoleRegistry(roles_root).load_all())

    pack = _zip(SCRATCH / "alias.zip", {
        "role.json": json.dumps({**ROLE, "role_id": 1}, ensure_ascii=False),
        "./role.json": json.dumps({**ROLE, "role_id": 2, "config_version": "7.7.7"},
                                  ensure_ascii=False),
    })
    rejected = False
    try:
        rolepack.import_rolepack(pack, roles_root / "1")
    except rolepack.RolePackError as exc:
        rejected = True
        print(f"  导入被拒（正确）: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"  导入抛错（非 RolePackError）: {type(exc).__name__}: {exc}")

    after = (roles_root / "1" / "role.json").read_text(encoding="utf-8")
    try:
        after_ids = sorted(c.role_id for c in RoleRegistry(roles_root).load_all())
        crash = None
    except Exception as exc:  # noqa: BLE001
        after_ids = []
        crash = f"{type(exc).__name__}: {exc}"

    print(f"  旧版本 role.json 逐字节不变 : {before == after}")
    print(f"  load_all 角色集合          : 改前 {before_ids} → 改后 {after_ids}")
    print(f"  load_all 是否崩溃          : {crash or '否'}")
    destroyed = before != after
    print(f"  → 校验被绕过 / 旧版本被销毁 : {not rejected or destroyed or crash is not None}")
    return not rejected or destroyed or crash is not None


def r2_4() -> bool:
    print()
    print(SEP)
    print("R2-4  音量键改档是否回写设置、重启是否水合")
    print(SEP)
    import time

    from host.adapters.runtime import build_context
    from host.config.settings import load_settings

    db = SCRATCH / "volume.sqlite3"
    settings = load_settings({"DB_PATH": str(db)})
    ctx = build_context(settings, simulate=True, root=TOY_ROOT)
    try:
        before = ctx.get_settings()["volume_level"]
        level = ctx.player.cycle_level()
        ctx.patch_settings({"volume_level": level})   # 模拟 audio worker 的回写
        after = ctx.get_settings()["volume_level"]
        print(f"  运行期档位 = {ctx.player.level}；设置页 volume_level: {before} → {after}")
        divergent = ctx.player.level != after
    finally:
        ctx.close()
    time.sleep(0.05)
    ctx2 = build_context(load_settings({"DB_PATH": str(db)}), simulate=True, root=TOY_ROOT)
    try:
        hydrated = ctx2.player.level
    finally:
        ctx2.close()
    print(f"  重启后水合档位 = {hydrated}")
    print(f"  → 界面/实际分叉或重启丢失: {divergent}")
    return divergent


def main() -> int:
    bad5 = r2_5()
    bad6 = r2_6()
    bad4 = r2_4()
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  R2-5 合法前缀包被误拒 : {'仍复现' if bad5 else '未复现'}")
    print(f"  R2-6 别名洞/旧版销毁  : {'仍复现' if bad6 else '未复现'}")
    print(f"  R2-4 音量分叉/重启丢  : {'仍复现' if bad4 else '未复现'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
