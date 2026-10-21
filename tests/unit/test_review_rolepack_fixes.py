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
from host.roles.schema import FACTORY_LIMITS

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
    # 限位取该角色的出厂值：``VALID_ROLE`` 里写死的 1400–1600 只对 role1 合法，
    # 复用到 role2/3 就是「放宽出厂机械限位」，会被 schema 按设计拒绝。
    factory_min, factory_max = FACTORY_LIMITS[role_id]
    (directory / "actions").mkdir(parents=True, exist_ok=True)
    (directory / "role.json").write_text(
        json.dumps(
            {
                **VALID_ROLE,
                "role_id": role_id,
                "config_version": config_version,
                "min_us": factory_min,
                "max_us": factory_max,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
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
    于是「必须含 role.json」被满足 —— 属于靠文件系统行为蒙混过关；Linux 上却会失败，
    平台行为不一致。
    """
    pack = _zip(tmp_path / "upper.zip", {
        "Role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}),
    })
    with pytest.raises(rolepack.RolePackError, match="缺少 role.json"):
        rolepack.validate_zip(pack)


def test_dot_prefixed_pack_is_still_accepted(tmp_path: Path) -> None:
    """`zip -r .` 生成的包带 `./` 前缀，属合法包，不得被误拒。"""
    pack = _zip(tmp_path / "dot.zip", {
        "./role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "./actions/wave.json": json.dumps({"schema": 1}),
    })

    rolepack.validate_zip(pack)                       # 不抛
    assert json.loads(rolepack.read_member(pack))["role_id"] == 1

    target = tmp_path / "roles" / "1"
    rolepack.import_rolepack(pack, target)
    assert (target / "role.json").is_file()


def test_dot_alias_collision_is_rejected(tmp_path: Path) -> None:
    """`role.json` 与 `./role.json` 在任何文件系统上都是同一路径 → 必须判冲突。

    否则「校验读到的那一份」（role_id=1，据此算目标目录）与「最后落盘的那一份」
    （role_id=2，后写覆盖）不是同一份内容：校验被绕过、落盘 role_id 与目录名错位，
    换入成功后 load_all 才发现双来源 → 500，且旧版本已被销毁（违反 AC-25）。
    """
    pack = _zip(tmp_path / "alias.zip", {
        "role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "./role.json": json.dumps({**VALID_ROLE, "role_id": 2}, ensure_ascii=False),
    })
    with pytest.raises(rolepack.RolePackError, match="归一化后冲突"):
        rolepack.validate_zip(pack)


def test_drive_letter_path_still_reports_absolute_path(tmp_path: Path) -> None:
    """盘符路径的诊断信息不能被冒号检查抢走（既有守卫的可读性回归）。"""
    pack = _zip(tmp_path / "drive.zip", {"C:/Windows/x.txt": "x"})
    with pytest.raises(rolepack.RolePackError, match="绝对路径"):
        rolepack.validate_zip(pack)


# ------------------------------------------------------------------ 成员名矩阵
# 把 `_norm_path`（折叠 `.` 段、保留大小写）与 `_fold_name`（再叠大小写折叠）的
# 全部分支固化成表。**这两个函数的语义差异极易在后续改动中被抹平**：
# 抹平大小写 → 误拒 `Role.json`；抹平 `.` 段 → 漏放 `./role.json` 或漏拦别名冲突。
ROLE_A = json.dumps(VALID_ROLE, ensure_ascii=False)
ROLE_B = json.dumps({**VALID_ROLE, "role_id": 2, "config_version": "7.7.7"}, ensure_ascii=False)

_MATRIX: tuple[tuple[str, dict[str, str], str], ...] = (
    ("单成员 role.json", {"role.json": ROLE_A}, "accept"),
    ("单成员 Role.json", {"Role.json": ROLE_A}, "missing"),
    ("`./` 前缀", {"./role.json": ROLE_A}, "accept"),
    ("`.\\` 前缀", {".\\role.json": ROLE_A}, "accept"),
    ("仅嵌套（缺根级）", {"dir/role.json": ROLE_A}, "missing"),
    ("根级 + 嵌套同名", {"role.json": ROLE_A, "dir/role.json": ROLE_B}, "accept"),
    ("仅目录条目 role.json/", {"role.json/": ""}, "missing"),
    ("跨目录同名 a/ 与 A/", {"a/role.json": ROLE_A, "A/role.json": ROLE_B}, "conflict"),
    ("`./Role.json` + role.json", {"./Role.json": ROLE_A, "role.json": ROLE_B}, "conflict"),
    ("`./role.json` + role.json", {"./role.json": ROLE_A, "role.json": ROLE_B}, "conflict"),
    ("role.json + Role.json", {"role.json": ROLE_A, "Role.json": ROLE_B}, "conflict"),
    ("`./dir/../role.json` 穿越", {"./dir/../role.json": ROLE_A}, "traversal"),
)


@pytest.mark.parametrize(("entries", "expect"), [c[1:] for c in _MATRIX],
                         ids=[c[0] for c in _MATRIX])
def test_member_name_matrix(tmp_path: Path, entries: dict[str, str], expect: str) -> None:
    pack = _zip(tmp_path / "matrix.zip", entries)

    if expect == "accept":
        rolepack.validate_zip(pack)
        # 取到的必须是**根级**那一份（嵌套同名不得截胡）
        assert json.loads(rolepack.read_member(pack))["role_id"] == 1
    elif expect == "missing":
        with pytest.raises(rolepack.RolePackError, match="缺少 role.json"):
            rolepack.validate_zip(pack)
    elif expect == "conflict":
        with pytest.raises(rolepack.RolePackError, match="归一化后冲突"):
            rolepack.validate_zip(pack)
    elif expect == "traversal":
        with pytest.raises(rolepack.RolePackError, match="穿越"):
            rolepack.validate_zip(pack)
    else:  # pragma: no cover - 表写错时立刻暴露
        raise AssertionError(f"未知期望值: {expect}")
