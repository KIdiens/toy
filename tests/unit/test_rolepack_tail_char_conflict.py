"""角色包成员名的Win32 尾随空格/点不得绕过冲突检测（第 6 组 R-3）。

缺陷本体（实测复现）：``_fold_name`` 只做路径归一化 + casefold，而 Win32 在落盘时
会**剥离路径段末尾的空格与点**。于是包内同时含 ``role.json`` 与 ``role.json.`` 时
冲突检测放行，``read_member`` 读到一份、``extract_safely`` 却把两份写到同一个文件
（实测三者只留一个目录项，内容为最后写入者），从而「校验的对象」与「落盘的对象」
不是同一份内容。

**测试形状**：全部用**真实 zip + 真实 ``import_rolepack``** 走完整路径，
不直接测 ``_fold_name`` 这个纯函数 —— 纯函数测试无法证明落盘与校验的一致性，
而一致性正是本缺陷的实质。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from host.roles import rolepack
from host.roles.registry import RoleRegistry


def _role(name: str, role_id: int, version: str = "1.0.0") -> dict:
    """按**真实 RoleConfig schema** 造合法配置（缺字段会被 pydantic 拒）。"""
    return {
        "schema_version": 1,
        "role_id": role_id,
        "name": name,
        "config_version": version,
        "min_us": 1400,
        "max_us": 1600,
        "neutral_us": 1500,
        "axes": ["左臂", "右臂", "头部左右", "腰部左右"],
        "tts_voice": "Cherry",
        "language": "zh",
        "actions": ["wave"],
    }


def _zip(path: Path, entries: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo(filename=name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return path


# ------------------------------------------------------------------ 缺陷本体
@pytest.mark.parametrize("smuggled", ["role.json.", "role.json "])
def test_tail_char_member_conflicts_with_role_json(tmp_path: Path, smuggled: str) -> None:
    """``role.json.`` / ``role.json␠`` 与 ``role.json`` 是同一文件，必须判冲突。"""
    pack = _zip(tmp_path / "evil.zip", {
        smuggled: json.dumps(_role("合法", 1), ensure_ascii=False),
        "role.json": json.dumps(_role("冒名", 2), ensure_ascii=False),
    })

    with pytest.raises(rolepack.RolePackError, match="归一化后冲突"):
        rolepack.validate_zip(pack)


def test_tail_char_smuggle_is_blocked_end_to_end(tmp_path: Path) -> None:
    """完整路径：冲突包必须**在落盘前**被拒，且不得产生任何角色目录。

    这是缺陷本体的直接对照：修复前该包会「校验通过 → read_member 读到合法/1 →
    落盘到 1/ 且内容却是冒名/2」，导致 registry 加载到冒名配置而动作全废。
    """
    pack = _zip(tmp_path / "evil.zip", {
        "role.json.": json.dumps(_role("合法", 1), ensure_ascii=False),
        "role.json": json.dumps(_role("冒名", 2), ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}, ensure_ascii=False),
    })
    roles_root = tmp_path / "roles"

    with pytest.raises(rolepack.RolePackError, match="归一化后冲突"):
        rolepack.import_rolepack(pack, roles_root / "1")

    assert not roles_root.exists() or list(roles_root.iterdir()) == [], (
        "被拒的包不得留下任何落盘产物（不得出现冒名顶替的半成品角色）"
    )


@pytest.mark.parametrize("smuggled", ["role.json.", "role.json "])
def test_tail_char_smuggle_cannot_swap_name_and_role_id(
    tmp_path: Path, smuggled: str
) -> None:
    """钉住缺陷的**实质**：冒名与 role_id 必须无法错配。

    修复前的后果是 registry 加载到 ``role_id=2``，但 ``_role_dir(2)`` 在磁盘上不存在，
    于是 ``action_path(2, 'wave') is None``、``action_files(2) == []`` —— 该角色动作全废。
    """
    pack = _zip(tmp_path / "evil.zip", {
        smuggled: json.dumps(_role("合法", 1), ensure_ascii=False),
        "role.json": json.dumps(_role("冒名", 2), ensure_ascii=False),
    })

    with pytest.raises(rolepack.RolePackError):
        config = json.loads(rolepack.read_member(pack).decode("utf-8"))
        # 若将来允许读出，至少不能出现「name 与 role_id 来自不同成员」的错配
        target = tmp_path / "roles" / str(config["role_id"])
        rolepack.import_rolepack(pack, target)

    registry = RoleRegistry(tmp_path / "roles")
    assert registry.action_path(2, "wave") is not None or not (
        tmp_path / "roles"
    ).exists(), "冒名包不得留下一个配置与目录不一致的角色"


# ------------------------------------------------------------------ 同源：后缀黑名单
@pytest.mark.parametrize("member", ["evil.py.", "evil.sh.", "evil.exe."])
def test_tail_char_cannot_bypass_executable_blacklist(tmp_path: Path, member: str) -> None:
    """尾随点同样能绕过后缀黑名单（实测落盘后就是 ``evil.py``），必须一并拒。"""
    pack = _zip(tmp_path / "evil.zip", {
        "role.json": json.dumps(_role("正常", 1), ensure_ascii=False),
        member: b"print(1)",
    })

    with pytest.raises(rolepack.RolePackError, match="拒绝可执行脚本"):
        rolepack.validate_zip(pack)


# ------------------------------------------------------------------ 防误伤（对照组）
def test_dot_slash_prefix_pack_is_not_broken(tmp_path: Path) -> None:
    """对照组：``./role.json`` 这类由 ``zip -r .`` 产生的合法包**不得被误伤**。"""
    pack = _zip(tmp_path / "legit.zip", {
        "./role.json": json.dumps(_role("正常角色", 1), ensure_ascii=False),
        "./actions/wave.json": json.dumps({"schema": 1}, ensure_ascii=False),
    })
    target = tmp_path / "roles" / "1"

    report = rolepack.import_rolepack(pack, target)

    assert report.files == 2
    landed = json.loads((target / "role.json").read_text(encoding="utf-8"))
    assert landed["role_id"] == 1 and landed["name"] == "正常角色", (
        "带 ./ 前缀的合法包必须原样落盘"
    )
    assert json.loads(rolepack.read_member(pack).decode("utf-8"))["role_id"] == 1


def test_existing_action_files_are_all_still_legal(toy_root: Path) -> None:
    """对照组：现有 3 个角色的**全部 7 个动作文件**必须仍被判为合法。"""
    actions_dir = toy_root / "content" / "roles"
    checked: list[str] = []
    for role_dir in sorted(p for p in actions_dir.iterdir() if p.is_dir()):
        for action in sorted(role_dir.glob("actions/*.json")):
            # 与解包时同一套判定：既不能被判成可执行脚本，路径也不能越界
            rolepack._check_name(f"./actions/{action.name}")
            assert rolepack._norm_path(f"./actions/{action.name}") == (
                f"actions/{action.name}"
            ), f"{action.name} 的归一化结果被改坏了"
            assert action.is_file() and json.loads(
                action.read_text(encoding="utf-8")
            ), f"{action} 内容为空或非法 JSON"
            checked.append(f"{role_dir.name}/{action.relative_to(role_dir).as_posix()}")

    assert len(checked) == 7, f"现有动作文件应为 7 个，实际 {len(checked)}: {checked}"


def test_role_json_alone_with_trailing_char_is_readable(tmp_path: Path) -> None:
    """对照组：**只有** ``role.json.``（无冲突）的包仍可读、仍含 role.json。

    剥离必须作用在「归一化」上，而不是把带尾随字符的成员一律拒掉—— 否则会把
    「Win32 上本来就等于 role.json 的那个文件」误伤成非法包。
    """
    pack = _zip(tmp_path / "solo.zip", {
        "role.json.": json.dumps(_role("唯一", 1), ensure_ascii=False),
    })

    payload = json.loads(rolepack.read_member(pack).decode("utf-8"))
    assert payload["name"] == "唯一"


# ------------------------------------------------------------------ 既有拦截项不得回归
@pytest.mark.parametrize("entries, expect", [
    ({"Role.json": "{}", "role.json": "{}"}, "归一化后冲突"),
    ({"role.json": "{}", "C:evil.json": "{}"}, "拒绝绝对路径"),
    ({"role.json": "{}", "sub/../../escape.json": "{}"}, "拒绝路径穿越"),
    ({"role.json": "{}", "role.json:evil": ""}, "拒绝含冒号"),
    ({"role.json": "{}", "../escape.json": "{}"}, "拒绝路径穿越"),
    ({"role.json": "{}", "evil.py": "x"}, "拒绝可执行脚本"),
])
def test_existing_rejections_still_reject(
    tmp_path: Path, entries: dict[str, str], expect: str
) -> None:
    """既有拦截项一个都不能因为本次改动而放行。"""
    pack = _zip(tmp_path / "cases.zip", entries)

    with pytest.raises(rolepack.RolePackError, match=expect):
        rolepack.validate_zip(pack)


def test_segment_normalisation_never_produces_parent_dir() -> None:
    """安全方向断言：归一化只可能把段**向内收敛**，绝不可能产出 ``..`` 段。

    若段剥离把 ``'.. '`` 变成 ``'..'``，就会凭空造出一条穿越路径。本条钉住方向。
    """
    for raw in (".. /evil.json", ".. . /evil.json", ".../evil.json", " .. /evil.json"):
        normalized = rolepack._norm_path(raw)
        assert ".." not in normalized.split("/"), (
            f"{raw!r} 归一化后竟含 '..' 段: {normalized!r}"
        )