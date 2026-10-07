"""角色包成员名的 Win32 保留设备名不得静默丢数据（第7 组）。

缺陷本体（本机 NTFS 实测，见 ``data/g7/probe_defect2_matrix.py``）：
``_check_name`` 只拒绝对路径 / 盘符 / 冒号 / ``..`` / 脚本后缀，**没拦 Win32 保留设备名**。
而 Win32 的设备名解析是「点号之前的基础名」匹配，与扩展名、目录层级、大小写都无关，
于是裸 ``NUL`` 打开的是 NUL 设备：往里写数据**静默丢弃** —— 不报错、
``import_rolepack`` 还照实报告 ``files=2``，但落盘目录里根本没有那个文件。

后果不是「多个文件」，而是**校验报告与落盘事实不一致**：调用方（含 web 层返回给
前端的导入结果）拿到「2 个文件」，实际只有 1 个；缺失的那份内容进了黑洞，无法排查。

实测边界（探针已逐个验证，勿扩大结论）：本机**只有裸 ``NUL``/``nul`` 真丢数据**，
``CON``/``AUX``/``PRN``/``COM1``/``LPT1``/``CLOCK$`` 与所有带扩展名的形式
（``aux.json``/``nul.json``…）都正常落盘为普通文件。测试按此边界断言：
设备名**一律拒绝**（跨机器/跨解析器的保守面），但绝不声称它们在本机都会丢数据。

**测试形状**：真实 zip + 真实 ``import_rolepack`` 走完整路径，不用 mock。
纯函数测试证明不了「报告与落盘一致」，而一致性正是本缺陷的实质。
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


def _landed_files(target: Path) -> set[str]:
    """落盘目录里真实存在的普通文件（相对路径，POSIX 分隔符）。"""
    return {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}


# ------------------------------------------------------------------ 缺陷本体
@pytest.mark.parametrize("member", [
    "NUL",# 裸名：实测静默丢数据的那一类
    "nul",                      # 设备名匹配大小写不敏感
    "sub/NUL",                  # 目录层级不影响：设备名只看路径段
    "CON",                      # 其余裸设备名（本机不丢数据，但同属 OS 契约）
    "AUX",
    "PRN",
    "COM1",
    "LPT9",
    "CLOCK$",
    "aux.json",                 # 带扩展名：Win32 仍按点号前的基础名匹配设备
    "nul.json",
    "COM1.log",
])
def test_reserved_device_name_member_is_rejected(tmp_path: Path, member: str) -> None:
    """保留设备名成员必须被拒，且**不得留下任何落盘残留**。"""
    pack = _zip(tmp_path / "evil.zip", {
        "role.json": json.dumps(_role("合法", 1), ensure_ascii=False),
        member: "PROBE-CONTENT" * 64,
    })

    with pytest.raises(rolepack.RolePackError, match="保留设备名"):
        rolepack.import_rolepack(pack, tmp_path / "roles" / "probe")

    # 失败必须保留「什么都没发生」：不能出现半途落盘的目录。
    assert not (tmp_path / "roles" / "probe").exists()


@pytest.mark.parametrize("alias", ["NUL ", "NUL.", "NUL .", "nul. "])
def test_device_name_alias_via_trailing_chars_is_rejected(tmp_path: Path,
                                                          alias: str) -> None:
    """``NUL␠`` / ``NUL.`` 这类别名不得绕过。

    与第 6 组 R-3（尾随空格/点绕过）**同源**：判设备名必须复用 ``_win32_strip``
    归一化后的段，否则 ``NUL␠`` 剥离后同样是 NUL 设备，却能过检查。
    实测（``data/g7/probe_defect2_alias.py``）：这四种写法落盘后目录里**都不出现**
    （写进 NUL 设备被丢弃），属必须拦的别名。

    反之**前导**空格（``"  NUL"``）实测落盘为真实普通文件（112 字节），Win32 只剥离
    **尾部**空格/点，故它不是设备名别名、不该被拦 —— 见下方``test_leading_space_*``
    的显式边界测试。
    """
    pack = _zip(tmp_path / "evil.zip", {
        "role.json": json.dumps(_role("合法", 1), ensure_ascii=False),
        alias: "PROBE-CONTENT" * 64,
    })

    with pytest.raises(rolepack.RolePackError, match="保留设备名"):
        rolepack.import_rolepack(pack, tmp_path / "roles" / "probe")


def test_leading_space_name_is_a_real_file_not_a_device(tmp_path: Path) -> None:
    """边界：**前导**空格不构成设备名别名，必须放行且真实落盘。

    这是对上一条的反向锁定：``_win32_strip`` 只处理**尾部**空格/点，Win32 亦然。
    若有人日后把归一化改成``strip(" .")``（误以为要更严），这条会转红 ——
    因为收紧到前导空格会把一个真实普通文件误判成设备名而拒绝，属误伤。
    """
    member = "  NUL"
    pack = _zip(tmp_path / "ok.zip", {
        "role.json": json.dumps(_role("合法", 1), ensure_ascii=False),
        member: "CONTENT" * 16,
    })
    target = tmp_path / "roles" / "probe"

    report = rolepack.import_rolepack(pack, target)

    assert _landed_files(target) == {"role.json", member}, _landed_files(target)
    assert (target / member).read_bytes() == b"CONTENT" * 16, "前导空格名字的内容应真实落盘"
    assert report.files == 2, report.files


def test_device_name_rejection_prevents_silent_content_loss(tmp_path: Path) -> None:
    """**核心判别力**：设备名成员不得再出现「报告 files 与实际落盘不一致」。

    这条直接盯住缺陷本体：旧行为下 ``import_rolepack`` 报 ``files=2`` 而目录里只有
    ``role.json``。修复后必须**在落盘前**就拒绝，而不是落盘后再对不上。
    """
    pack = _zip(tmp_path / "evil.zip", {
        "role.json": json.dumps(_role("合法", 1), ensure_ascii=False),
        "NUL": "PROBE-CONTENT" * 64,
    })

    target = tmp_path / "roles" / "probe"
    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(pack, target)

    # 拒绝后不得有任何文件落盘（含设备名的0 字节占位）。
    if target.exists():
        assert _landed_files(target) == {"role.json"}, _landed_files(target)


# ------------------------------------------------------------------ 误伤面
@pytest.mark.parametrize("member", [
    "actions/greet.json",
    "actions/nod.json",
    "actions/wave.json",
    "audio/greeting.wav",
    "notes.txt",
    "nullable.json",            # 前缀相近但不是设备名
    "console.json",             # 以 CON 开头但不是设备名（不能按前缀拦）
    "COM10.json",               # COM1x 不是设备名（COM 只有 1~9）
    "LPT0.json",                # LPT 只有 1~9
    "analysis/auxiliary.json",  # 目录名含 aux 但整体不是设备名
])
def test_legitimate_member_names_are_not_mistaken_for_devices(tmp_path: Path,
                member: str) -> None:
    """合法成员名不得被设备名检查误伤 —— 尤其不能按「前缀」粗暴匹配。

    ``console.json`` / ``COM10.json`` / ``auxiliary.json`` 是本条误伤面的代表：
    它们以设备名开头或包含设备名，但**基础名整体不是**保留设备名，必须放行。
    """
    pack = _zip(tmp_path / "ok.zip", {
        "role.json": json.dumps(_role("合法", 1), ensure_ascii=False),
        member: "CONTENT" * 16,
    })
    target = tmp_path / "roles" / "probe"

    report = rolepack.import_rolepack(pack, target)

    # 报告与落盘必须一致（files=2 即 role.json + 本成员）。
    assert report.files == 2, report.files
    landed = _landed_files(target)
    assert landed == {"role.json", member}, landed


def test_real_repository_content_still_imports(tmp_path: Path) -> None:
    """仓库真实内容（3 角色 / 7 动作文件）打包导入必须**全部合法**。"""
    repo_roles = Path(__file__).resolve().parents[2] / "content" / "roles"
    assert repo_roles.is_dir(), f"真实角色内容缺失: {repo_roles}"

    packs = 0
    for role_dir in sorted(p for p in repo_roles.iterdir() if p.is_dir()):
        entries: dict[str, str | bytes] = {}
        for src in sorted(role_dir.rglob("*")):
            if src.is_file():
                rel = src.relative_to(role_dir).as_posix()
                entries[rel] = src.read_bytes()
        assert entries, f"角色 {role_dir.name} 没有任何文件"

        pack = _zip(tmp_path / f"{role_dir.name}.zip", entries)
        report = rolepack.import_rolepack(pack, tmp_path / "out" / role_dir.name)
        assert report.files == len(entries), (role_dir.name, report.files, len(entries))
        packs += 1

    assert packs == 3, f"预期 3 个角色包，实际 {packs}"

    # 且换入后的目录必须能被真实加载器读进去（端到端不炸）。
    registry = RoleRegistry(tmp_path / "out")
    loaded = registry.load_all()
    assert loaded, "真实角色包换入后加载器读不到任何角色"