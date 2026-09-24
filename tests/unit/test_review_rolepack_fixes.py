"""复查整改（第一批）回归护栏：角色包导入的原子性与成员名归一化。

覆盖三条：
- 换入失败时必须**还原旧版本**（否则「导入失败」的后果是「角色凭空消失」，违反 AC-25）
- `load_all` 必须跳过 `<role>.bak` / `<role>.incoming` 残留（否则同一 role_id 双来源
  会让整个 load_all 抛错、服务起不来；版本号相同时更会静默加载错误配置）
- 成员名归一化冲突必须拒绝（`role.json` 与 `Role.json` 在大小写不敏感文件系统上落到
  同一文件 → 「校验的对象」与「落盘的对象」不是同一份内容，AC-25 被绕过）
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from host.roles import rolepack
from host.roles.registry import RoleRegistry

VALID_ROLE = {
    "schema_version": 1,
    "role_id": 1,
    "name": "小星",
    "config_version": "9.9.9",
    "min_us": 1400,
    "max_us": 1600,
    "neutral_us": 1500,
    "axes": ["左臂", "右臂", "头部左右", "腰部左右"],
    "actions": ["wave"],
}


def _zip(path: Path, entries: dict[str, str], *,
         compression: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _write_role(directory: Path, *, role_id: int, config_version: str) -> None:
    (directory / "actions").mkdir(parents=True, exist_ok=True)
    (directory / "role.json").write_text(
        json.dumps({**VALID_ROLE, "role_id": role_id, "config_version": config_version},
                   ensure_ascii=False), encoding="utf-8")
    (directory / "actions" / "wave.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")


# ------------------------------------------------------------------ 原子性
def test_failed_swap_restores_the_old_version(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "roles" / "1"
    _write_role(target, role_id=1, config_version="1.0.0")
    old_blob = (target / "role.json").read_text(encoding="utf-8")

    pack = _zip(tmp_path / "pack.zip", {
        "role.json": json.dumps({**VALID_ROLE, "config_version": "7.7.7"}, ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}),
    })

    real_rename = Path.rename

    def flaky_rename(self: Path, dest):
        if self.name.endswith(".incoming"):
            raise OSError("模拟换入失败")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    # 必须是本模块对外承诺的 RolePackError（web 层据此回 400）；抛裸 OSError 会漏成纯文本 500
    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(pack, target)

    assert target.is_dir(), "换入失败后旧角色目录必须被还原回原位（AC-25）"
    assert (target / "role.json").read_text(encoding="utf-8") == old_blob, "旧版本内容被破坏"
    assert not (tmp_path / "roles" / "1.incoming").exists(), "残留目录未清理"


def test_successful_import_leaves_no_residue(tmp_path: Path) -> None:
    target = tmp_path / "roles" / "1"
    _write_role(target, role_id=1, config_version="1.0.0")
    pack = _zip(tmp_path / "pack.zip", {
        "role.json": json.dumps({**VALID_ROLE, "config_version": "2.0.0"}, ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}),
    })

    rolepack.import_rolepack(pack, target)

    names = sorted(p.name for p in (tmp_path / "roles").iterdir())
    assert names == ["1"], f"导入成功后不应有残留目录：{names}"
    assert "2.0.0" in (target / "role.json").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 残留排除
def test_load_all_ignores_residue_directories(tmp_path: Path) -> None:
    root = tmp_path / "roles"
    _write_role(root / "2", role_id=2, config_version="1.0.0")
    _write_role(root / "2.bak", role_id=2, config_version="7.7.7")      # 崩溃残留
    _write_role(root / "3.incoming", role_id=3, config_version="1.0.0")  # 崩溃残留

    configs = RoleRegistry(root).load_all()

    assert [c.role_id for c in configs] == [2], "残留目录被当成角色加载了"
    assert configs[0].config_version == "1.0.0", "读到了残留里的配置"


# ------------------------------------------------------------------ 成员名归一化
def test_case_colliding_member_names_are_rejected(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "collide.zip", {
        "role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "Role.json": json.dumps({**VALID_ROLE, "role_id": 2}, ensure_ascii=False),
    })
    with pytest.raises(rolepack.RolePackError, match="归一化后冲突"):
        rolepack.validate_zip(pack)


def test_ntfs_ads_style_member_name_is_rejected(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "ads.zip", {"role.json:evil": "x"})
    with pytest.raises(rolepack.RolePackError, match="冒号"):
        rolepack.validate_zip(pack)


def test_uppercase_role_json_alone_does_not_satisfy_the_requirement(tmp_path: Path) -> None:
    """只含 `Role.json` 的包必须被拒。

    大小写不敏感的文件系统上，`incoming / "role.json"` 会命中 `Role.json`，
    于是「必须含 role.json」被满足 —— 属于靠文件系统行为蒙混过关。
    """
    pack = _zip(tmp_path / "upper.zip", {
        "Role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}),
    })
    with pytest.raises(rolepack.RolePackError, match="缺少 role.json"):
        rolepack.validate_zip(pack)


def test_drive_letter_path_still_reports_absolute_path(tmp_path: Path) -> None:
    """盘符路径的诊断信息不能被冒号检查抢走（既有守卫的可读性回归）。"""
    pack = _zip(tmp_path / "drive.zip", {"C:/Windows/x.txt": "x"})
    with pytest.raises(rolepack.RolePackError, match="绝对路径"):
        rolepack.validate_zip(pack)
