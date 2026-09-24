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
# 换入过程留下的残留目录后缀。它们不是角色，加载方必须跳过（见 registry.load_all）：
# 被当成角色读入会导致同一 role_id 双来源 → ConfigError；版本号相同时更会静默错读。
RESIDUE_SUFFIXES = (".bak", ".incoming")


class RolePackError(ConfigError):
    """角色包校验失败（结构 / 安全 / 体量）。失败必须保留旧版本。"""


@dataclass(frozen=True, slots=True)
class ExtractReport:
    files: int
    total_bytes: int
    role_json: str = ""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _fold_name(name: str) -> str:
    """成员名的归一化键：统一分隔符 + 去掉尾部斜杠 + 大小写折叠。

    大小写不敏感的文件系统（Windows / macOS）上，``role.json`` 与 ``Role.json`` 会落到
    **同一个文件**。若不归一化去重，「校验读到的那个成员」与「最后落盘的那个成员」可以
    不是同一份内容 —— 校验就被绕过了。
    """
    return name.replace("\\", "/").rstrip("/").casefold()


def _check_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise RolePackError(f"拒绝绝对路径: {name}")
    if ":" in normalized:
        # 盘符之外的冒号：NTFS 数据流（如 `role.json:evil`）。它会让落盘的 `role.json`
        # 变成一个 0 字节占位，从而「满足」必须含 role.json 的检查。
        raise RolePackError(f"拒绝含冒号的成员名: {name}")
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
        seen: dict[str, str] = {}
        for info in infos:
            _check_name(info.filename)
            folded = _fold_name(info.filename)
            if folded in seen:
                raise RolePackError(
                    f"成员名归一化后冲突: {info.filename!r} 与 {seen[folded]!r}"
                    "（大小写不敏感文件系统上会落到同一文件，导致「校验的对象」"
                    "与「落盘的对象」不是同一份内容）"
                )
            seen[folded] = info.filename
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
        try:
            incoming.rename(target)
        except OSError as exc:
            # 换入失败必须把旧版本**还原回原位**。否则「导入失败」的后果是「角色凭空消失」：
            # 旧版本只剩在 `<role>.bak` 里，而加载方不会读它（违反 AC-25「失败保留旧版本」）。
            if backup.exists() and not target.exists():
                try:
                    backup.rename(target)
                except OSError as restore_exc:
                    raise RolePackError(
                        f"角色包换入失败，且旧版本还原也失败：旧版本在 {backup}，"
                        f"目标位 {target} 当前缺失，需人工恢复。"
                        f"（换入错误: {exc}；还原错误: {restore_exc}）"
                    ) from exc
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return ExtractReport(files=report.files, total_bytes=report.total_bytes,
                             role_json=str(target / ROLE_JSON))
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
