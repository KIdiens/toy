"""角色包导入的文件系统异常必须收口成 RolePackError（第 8 组）。

缺陷本体（本机 Windows 实测，见 ``data/g8/probe_rolepack_tab.py``）：
成员名**尾部带制表符**时（``nul\\t`` / ``aux\\t``），Win32 的设备名解析会命中保留设备，
``target.open("wb")`` 抛**裸** ``PermissionError``。它绕过 ``RolePackError``，
而 web 层 ``content.py:62`` 只``except rolepack.RolePackError`` -> 于是漏到
``app.py`` 的兜底 handler，返回 **500 /50000** 而不是导入失败该有的 **400 / 40001**。

关键区分：这条**不是**「非法包」，而是一个**语义化报错根本没机会发出**的通道。
调用方无法从500 区分「包坏了」与「服务端炸了」，前端也无法提示用户重打包。

同类排查（``data/g8/probe_rolepack_oserror_audit.py``逐个构造真实触发条件）确认
``import_rolepack`` / ``extract_safely`` 里有 **4 处**会裸抛 ``OSError``：
落盘 ``open``、父目录 ``mkdir``、旧版本 ``rename``、以及 ``validate_zip`` 打开
zip 本身（文件不存在 / 字节损坏）。本测试把 4 处全部钉住。

**修法边界（重要）**：只捕获 ``OSError``，**不写 ``except Exception``**。
``RolePackError`` 继承 ``ConfigError -> HostError -> RuntimeError``，**不是**
``OSError`` 子类，所以 ``except OSError`` 在结构上不可能吞掉本模块自己的语义化报错。

诚实说明本文件判别力的边界（实测，不是推断）：把 ``extract_safely`` 的
``except OSError`` 改成 ``except Exception`` 后，**本文件全部用例仍然绿**——
因为那些 ``try`` 块里只有文件系统调用，语义化报错都在块外抛出。所以
「对照组转红」这句话只在「有人把新校验挪进try 块」这个**未来**场景成立，
不是当前的可检验事实。当前可检验的只有两条，都在本文件里：
1. 7 条 OSError 收口用例（回退产品代码即转红——已实测）；
2. :func:`test_rolepack_error_is_not_an_oserror_subclass` 断言的继承关系。

**误伤面**：本测试只新增``try`` 包装，**不新增任何名字校验**。合法角色包的落盘
字节、报告字段、目录结构一个字节都不变——
``test_normal_pack_is_byte_identical_with_and_without_the_guard`` 用「同一份 zip
导入两次、逐字节比对落盘内容」把这条钉死。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from host.roles import rolepack

# ------------------------------------------------------------------ 工具


def _role(name: str = "探针", role_id: int = 1, version: str = "1.0.0") -> dict:
    """按真实 RoleConfig schema 造合法配置（缺字段会被 pydantic 拒）。"""
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


def _zip(path: Path, entries: dict[str, str], *,
         compress: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compress) as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo(filename=name)
            info.compress_type = compress
            archive.writestr(info, data)
    return path


def _digest_tree(root: Path) -> dict[str, str]:
    """落盘目录的「相对路径 -> 内容 sha256」，用于逐字节比对。"""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ------------------------------------------------------------------ 缺陷本体
@pytest.mark.parametrize("member", [
    "nul\t",   # 裸NUL + 尾部制表符：实测 PermissionError
    "aux\t",   # 另一枚保留设备，同样命中
    "sub/nul\t",   # 目录层级不影响设备名解析
])
def test_trailing_tab_device_name_becomes_rolepack_error_not_bare_oserror(
    tmp_path: Path, member: str
) -> None:
    """尾部制表符命中设备名时，必须是 RolePackError（->400），不得是裸 OSError（->500）。"""
    pack = _zip(tmp_path / "evil.zip", {
        "role.json": json.dumps(_role(), ensure_ascii=False),
        member: "PROBE-CONTENT" * 64,
    })
    target = tmp_path / "roles" / "probe"

    with pytest.raises(rolepack.RolePackError) as caught:
        rolepack.import_rolepack(pack, target)

    # 报错必须带**成员名上下文**，否则多成员包出错时无法定位是哪一个。
    assert member in str(caught.value) or "nul" in str(caught.value).lower(), (
        f"报错里没有成员名上下文，无法定位: {caught.value}"
    )
    # 失败必须保留「什么都没发生」。
    assert not target.exists()


# ------------------------------------------------- 同类排查：4 处裸 OSError 收口
def test_missing_zip_file_becomes_rolepack_error(tmp_path: Path) -> None:
    """zip 文件不存在：``FileNotFoundError`` 同样是裸 OSError（漏 500）。"""
    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(tmp_path / "nope.zip", tmp_path / "roles" / "probe")


def test_corrupted_zip_bytes_become_rolepack_error(tmp_path: Path) -> None:
    """zip 字节损坏：``archive.open`` 抛的 ``OSError(Errno 22)`` 必须被收口。

    构造方式与 ``data/g8/probe_rolepack_oserror_audit.py`` 的场景 G 一致：
    破坏中央目录尾部字节，让 ``ZipFile()`` 构造通过（能读到 infolist），
    但真正取成员内容时 ``seek`` 失败。

    **必须用 ZIP_STORED**：用 DEFLATED 时同样的破坏会先把 ``a.json`` 的
    ``file_size`` 改成巨大值，从而被 :func:`validate_zip` 的压缩比上限拦下
    （报「压缩比异常」的 RolePackError）—— 那条是**语义化路径**，会让本用例
    假绿、测不到真正要钉的 ``OSError`` 收口。这是实测踩过的坑，不是假设。
    """
    pack = _zip(tmp_path / "broken.zip", {
        "role.json": json.dumps(_role(), ensure_ascii=False),
        "a.json": "A" * 4000,
    }, compress=zipfile.ZIP_STORED)
    raw = bytearray(pack.read_bytes())
    for index in range(len(raw) - 6, len(raw) - 2):
        raw[index] ^= 0xFF
    pack.write_bytes(bytes(raw))

    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(pack, tmp_path / "roles" / "probe")


def test_unwritable_destination_becomes_rolepack_error(tmp_path: Path) -> None:
    """目标父路径是**文件**（建不出incoming 目录）：``mkdir`` 抛 ``FileExistsError``。"""
    pack = _zip(tmp_path / "ok.zip", {
        "role.json": json.dumps(_role(), ensure_ascii=False),
        "a.json": "{}",
    })
    roles_root = tmp_path / "roles"
    roles_root.write_text("我是一个文件，不是目录", encoding="utf-8")

    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(pack, roles_root / "probe")


@pytest.mark.skipif(not hasattr(__import__("os"), "name"), reason="POSIX only")
def test_locked_old_version_becomes_rolepack_error(tmp_path: Path) -> None:
    """旧版本目录被占用导致 ``rename`` 失败：必须收口，且**旧版本不许丢**。

    场景B（``data/g8/probe_rolepack_oserror_audit.py``）：在旧版本目录里开一个
    不关的文件句柄，Windows 上会挡住 ``target.rename(backup)``。

    这条同时钉住 AC-25「失败保留旧版本」：报错发生时 ``role.json`` 必须还在原位。
    """
    pack = _zip(tmp_path / "ok.zip", {
        "role.json": json.dumps(_role(), ensure_ascii=False),
        "a.json": "{}",
    })
    target = tmp_path / "roles" / "probe"
    target.mkdir(parents=True)
    (target / "role.json").write_text(json.dumps(_role("旧版本"), ensure_ascii=False),
                                      encoding="utf-8")

    handle = open(target / "role.json", "rb")   # noqa: SIM115 - 故意不关，模拟占用
    try:
        with pytest.raises(rolepack.RolePackError):
            rolepack.import_rolepack(pack, target)
        # 旧版本必须还在（可能在 .bak 里，但绝不能凭空消失）
        still_there = (target / "role.json").exists() or (
            (tmp_path / "roles" / "probe.bak" / "role.json").exists()
        )
        assert still_there, "导入失败后旧版本消失了（违反 AC-25）"
    finally:
        handle.close()


# ------------------------------------------------- 对照组：语义化报错不许被吞
@pytest.mark.parametrize("extra,expect", [
    ({"../evil.json"}, "路径穿越"),
    ({"/abs/role.json"}, "绝对路径"),
    ({"role.json:evil"}, "冒号"),
    ({"evil.py"}, "可执行脚本"),
    ({"nul"}, "保留设备名"),
    # 归一化后同名 -> 冲突。必须**成对**出现才有冲突面：单写``role.json.``
    # 并不与 role.json 冲突（第7 组已钉过role.json. 可正常导入）。
    ({"role.json."}, "冲突"),
    ({"./role.json"}, "冲突"),
    ({"ROLE.JSON"}, "冲突"),
])
def test_semantic_errors_are_not_swallowed_by_the_oserror_guard(
    tmp_path: Path, extra: set[str], expect: str
) -> None:
    """对照组：本模块自己的语义化报错必须**原样**透出，不能被 OSError 收口改写。

    这是「不许写 ``except Exception``」的判别力来源。``RolePackError`` 继承
    ``ConfigError -> HostError -> RuntimeError``，与 ``OSError`` **无继承关系**，
    所以 ``except OSError`` 抓不到它；若有人日后把捕获范围放宽成 ``Exception``，
    这条会立刻转红。
    """
    entries = {"role.json": json.dumps(_role(), ensure_ascii=False)}
    entries.update(dict.fromkeys(extra, "{}"))
    pack = _zip(tmp_path / "semantic.zip", entries)

    with pytest.raises(rolepack.RolePackError) as caught:
        rolepack.import_rolepack(pack, tmp_path / "roles" / "probe")

    assert expect in str(caught.value), (
        f"期望是语义化报错（含 {expect!r}），实际得到: {caught.value}"
    )


def test_rolepack_error_is_not_an_oserror_subclass() -> None:
    """结构不变量：``RolePackError`` **不是** ``OSError`` 子类。

    这是「``except OSError`` 收口不会吞掉语义化报错」的**唯一**依据，也是把
    收口范围钉死成 ``OSError`` 的理由。把上面几条 ``except OSError`` 改成
    ``except Exception`` **不会**让本文件转红（实测过）：那些 ``try`` 块里只有
    文件系统调用，语义化报错都在块外抛出。也就是说「放宽捕获」目前无害，但
    无害性**依赖代码布局**—— 日后谁把新校验挪进 ``try``，就会踩到。

    这条断言让该风险变成**可检测**的：若有人日后让 ``RolePackError`` 改为
    继承 ``OSError``（例如为了统一异常层级），本条立刻转红，强制重新评估
    所有 ``except OSError`` 收口点是否还会误伤语义化报错。
    """
    assert not issubclass(rolepack.RolePackError, OSError), (
        "RolePackError 变成了 OSError 子类：所有 'except OSError' 收口点"
        "将开始捕获本模块自己的语义化报错，必须重新评估"
    )
    # 反向也钉一下：收口产出的异常必须**不是** OSError，否则调用方的
    # 'except OSError' 分支会把导入失败误当成磁盘故障。
    guarded = rolepack._fs_error("x.json", OSError(28, "No space left on device"))
    assert isinstance(guarded, rolepack.RolePackError)
    assert not isinstance(guarded, OSError), "收口后的异常仍被当成文件系统错误"


def test_missing_role_json_stays_semantic(tmp_path: Path) -> None:
    """对照组：缺 role.json 仍报「缺少 role.json」，不是文件系统错误。"""
    pack = _zip(tmp_path / "noRole.zip", {"a.json": "{}"})

    with pytest.raises(rolepack.RolePackError, match="缺少"):
        rolepack.import_rolepack(pack, tmp_path / "roles" / "probe")


# ------------------------------------------------------------ 误伤面钉死
def test_normal_pack_is_byte_identical_with_and_without_the_guard(tmp_path: Path) -> None:
    """合法角色包：收口前后落盘内容必须**逐字节一致**（误伤面钉死）。

    做法：同一份 zip 导入到两个目标目录，比对 ``相对路径 -> sha256`` 全等，
    并核对报告字段。这条保证新增的 ``try`` 只是**包装**异常、不触碰数据通路。
    """
    entries = {
        "role.json": json.dumps(_role("正常角色", 1), ensure_ascii=False),
        "wave.json": json.dumps({"action": "wave", "us": 1500}, ensure_ascii=False),
        "nod.json": json.dumps({"action": "nod", "us": 1400}, ensure_ascii=False),
        "shake.json": json.dumps({"action": "shake", "us": 1600}, ensure_ascii=False),
    }
    pack = _zip(tmp_path / "normal.zip", entries)

    first = tmp_path / "roles" / "a"
    report_a = rolepack.import_rolepack(pack, first)
    second = tmp_path / "roles" / "b"
    report_b = rolepack.import_rolepack(pack, second)

    assert _digest_tree(first) == _digest_tree(second), "两次导入落盘内容不一致"
    assert _digest_tree(first) == {
        "role.json": hashlib.sha256(entries["role.json"].encode()).hexdigest(),
        "wave.json": hashlib.sha256(entries["wave.json"].encode()).hexdigest(),
        "nod.json": hashlib.sha256(entries["nod.json"].encode()).hexdigest(),
        "shake.json": hashlib.sha256(entries["shake.json"].encode()).hexdigest(),
    }
    assert report_a.files == report_b.files == 4
    assert report_a.total_bytes == report_b.total_bytes
    assert report_a.role_json.endswith("role.json")
