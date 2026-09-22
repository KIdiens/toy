"""角色包 zip 安全解包与导入（AC-25）。

导入必须**先解到临时目录、校验通过后原子切换**：

1. 成员校验：拒绝 ``../``、绝对路径、Windows 盘符、符号链接、可执行脚本后缀；
2. 体量校验：文件数上限、解压总大小上限、单文件压缩比上限（防压缩炸弹）；
3. 落盘后再校验必须含 ``role.json``（否则不是角色包）；
4. 通过后把生效目录原子替换；**任一步失败都保留旧版本**（不半途污染现网目录）。
"""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from host.core.errors import ConfigError

MAX_FILES = 200
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024
MAX_SINGLE_UNCOMPRESSED = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
FORBIDDEN_SUFFIXES = (
    ".sh", ".bat", ".cmd", ".exe", ".dll", ".so", ".dylib",
    ".py", ".ps1", ".js", ".mjs", ".com", ".scr",
)
ROLE_JSON = "role.json"


class RolePackError(ConfigError):
    """角色包校验失败（结构 / 安全 / 体量）。失败必须保留旧版本。"""


@dataclass(frozen=True, slots=True)
class ExtractReport:
    files: int
    total_bytes: int
    role_json: str = ""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _check_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise RolePackError(f"拒绝绝对路径: {name}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise RolePackError(f"拒绝路径穿越: {name}")
    if normalized.lower().endswith(FORBIDDEN_SUFFIXES):
        raise RolePackError(f"拒绝可执行脚本: {name}")


def validate_zip(zip_path: str | Path) -> list[zipfile.ZipInfo]:
    """只读校验：全量检查后再放行解压，避免边解边炸。"""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise RolePackError(f"损坏的 zip: {exc}") from exc
    with archive:
        infos = archive.infolist()
        files = [i for i in infos if not i.is_dir()]
        if len(files) > MAX_FILES:
            raise RolePackError(f"文件数超上限: {len(files)} > {MAX_FILES}")
        total = 0
        for info in infos:
            _check_name(info.filename)
            if _is_symlink(info):
                raise RolePackError(f"拒绝符号链接: {info.filename}")
            if info.file_size > MAX_SINGLE_UNCOMPRESSED:
                raise RolePackError(f"单文件超上限: {info.filename}")
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > MAX_COMPRESSION_RATIO:
                raise RolePackError(f"压缩比异常: {info.filename} ({ratio:.0f}x)")
            total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise RolePackError(f"解压总大小超上限: {total} > {MAX_TOTAL_UNCOMPRESSED}")
        return infos


def read_member(zip_path: str | Path, name: str = ROLE_JSON) -> bytes:
    """在**不落盘**的前提下读取包内单个文件（先全量校验，防边读边炸）。

    用于导入前读出 ``role.json`` 以确定目标角色目录并做 schema 校验，
    避免「先替换再发现非法」导致现网角色被污染。
    """
    validate_zip(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        try:
            return archive.read(name)
        except KeyError as exc:
            raise RolePackError(f"角色包缺少 {name}") from exc


def extract_safely(zip_path: str | Path, dest: str | Path) -> ExtractReport:
    """把通过校验的 zip 解到 ``dest``（``dest`` 由调用方保证是临时目录）。"""
    destination = Path(dest).resolve()
    infos = validate_zip(zip_path)
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with zipfile.ZipFile(zip_path) as archive:
        for info in infos:
            target = (destination / info.filename).resolve()
            if destination not in target.parents and target != destination:
                raise RolePackError(f"越界写入被拒: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            count += 1
            total += info.file_size
    return ExtractReport(files=count, total_bytes=total)


def import_rolepack(zip_path: str | Path, target_dir: str | Path) -> ExtractReport:
    """导入角色包到 ``target_dir``：先解临时目录校验，通过后原子切换，失败保留旧版本。"""
    target = Path(target_dir)
    incoming = target.with_name(target.name + ".incoming")
    backup = target.with_name(target.name + ".bak")
    shutil.rmtree(incoming, ignore_errors=True)
    try:
        report = extract_safely(zip_path, incoming)
        role_json = incoming / ROLE_JSON
        if not role_json.is_file():
            raise RolePackError(f"角色包缺少 {ROLE_JSON}")
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            target.rename(backup)
        incoming.rename(target)
        shutil.rmtree(backup, ignore_errors=True)
        return ExtractReport(files=report.files, total_bytes=report.total_bytes,
                             role_json=str(target / ROLE_JSON))
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
