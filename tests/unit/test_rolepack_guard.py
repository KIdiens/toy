"""角色包 zip 安全导入（AC-25）：拒绝穿越 / 绝对路径 / 符号链接 / 脚本 / 压缩炸弹；
校验通过才原子切换，任一步失败**保留旧版本**。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from host.roles import rolepack

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


def _zip(path: Path, entries: dict[str, str | bytes], *,
         compression: int = zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _valid_pack(tmp_path: Path) -> Path:
    return _zip(tmp_path / "pack.zip", {
        "role.json": json.dumps(VALID_ROLE, ensure_ascii=False),
        "actions/wave.json": json.dumps({"schema": 1}, ensure_ascii=False),
    })


# ------------------------------------------------------------------ 放行
def test_valid_pack_imports_and_switches_atomically(tmp_path: Path) -> None:
    target = tmp_path / "roles" / "1"
    report = rolepack.import_rolepack(_valid_pack(tmp_path), target)

    assert report.files == 2
    assert (target / "role.json").is_file()
    assert json.loads((target / "role.json").read_text(encoding="utf-8"))["role_id"] == 1
    assert not (tmp_path / "roles" / "1.incoming").exists()
    assert not (tmp_path / "roles" / "1.bak").exists()


def test_read_member_returns_role_json(tmp_path: Path) -> None:
    payload = json.loads(rolepack.read_member(_valid_pack(tmp_path)).decode("utf-8"))
    assert payload["name"] == "小星"


# ------------------------------------------------------------------ 拒绝（结构 / 安全）
def test_rejects_path_traversal(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "evil.zip", {"../escape.json": "{}"})
    with pytest.raises(rolepack.RolePackError, match="路径穿越"):
        rolepack.validate_zip(pack)


def test_rejects_absolute_path(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "abs.zip", {"/etc/passwd": "root:x:0:0"})
    with pytest.raises(rolepack.RolePackError, match="绝对路径"):
        rolepack.validate_zip(pack)


def test_rejects_windows_drive_path(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "drive.zip", {"C:/Windows/x.txt": "x"})
    with pytest.raises(rolepack.RolePackError, match="绝对路径"):
        rolepack.validate_zip(pack)


def test_rejects_symlink_member(tmp_path: Path) -> None:
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16  # S_IFLNK
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(rolepack.RolePackError, match="符号链接"):
        rolepack.validate_zip(path)


@pytest.mark.parametrize("name", ["run.bat", "install.sh", "payload.exe", "hook.py"])
def test_rejects_executable_suffix(tmp_path: Path, name: str) -> None:
    pack = _zip(tmp_path / "exe.zip", {name: "echo hi"})
    with pytest.raises(rolepack.RolePackError, match="可执行脚本"):
        rolepack.validate_zip(pack)


def test_rejects_too_many_files(tmp_path: Path) -> None:
    entries = {f"f{i}.txt": "x" for i in range(rolepack.MAX_FILES + 1)}
    with pytest.raises(rolepack.RolePackError, match="文件数超上限"):
        rolepack.validate_zip(_zip(tmp_path / "many.zip", entries))


def test_rejects_compression_bomb(tmp_path: Path) -> None:
    bomb = _zip(tmp_path / "bomb.zip", {"blob.bin": b"\x00" * (5 * 1024 * 1024)})
    with pytest.raises(rolepack.RolePackError, match="压缩比异常"):
        rolepack.validate_zip(bomb)


def test_rejects_corrupted_zip(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    with pytest.raises(rolepack.RolePackError, match="损坏的 zip"):
        rolepack.validate_zip(broken)


def test_rejects_pack_without_role_json(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "norole.zip", {"actions/wave.json": "{}"})
    with pytest.raises(rolepack.RolePackError, match="缺少 role.json"):
        rolepack.read_member(pack)


# ------------------------------------------------------------------ 失败保留旧版本
def test_failed_import_keeps_previous_version(tmp_path: Path) -> None:
    target = tmp_path / "roles" / "1"
    rolepack.import_rolepack(_valid_pack(tmp_path), target)  # 先装一个正常版本
    before = (target / "role.json").read_text(encoding="utf-8")

    bad_pack = _zip(tmp_path / "bad.zip", {"role.json": "{}", "../evil": "x"})
    with pytest.raises(rolepack.RolePackError):
        rolepack.import_rolepack(bad_pack, target)

    assert (target / "role.json").read_text(encoding="utf-8") == before, "失败必须保留旧版本"
    assert not (tmp_path / "roles" / "1.incoming").exists()
    assert not (tmp_path / "roles" / "1.bak").exists()
