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


def _win32_strip(segment: str) -> str:
    """Win32 归一化：**路径段末尾**的空格与点会被文件系统剥离。

    实测（本机 NTFS）：写入 ``role.json`` / ``role.json.`` / ``role.json␠`` 三个名字，
    目录里只剩一个 ``role.json``，内容为**最后写入**的那份。即这三个名字在 Win32 上
    是同一个文件，不存在「第三个文件」。

    因此凡是要回答「这两个成员名会不会落到同一个文件」的判定，都必须先过这一层：
    ``role.json.`` 与 ``role.json`` 是同一文件，``role.json␠`` 同理。
    """
    return segment.rstrip(" .")


def _norm_path(name: str) -> str:
    """路径归一化：统一分隔符 + 折叠空段与 ``.`` 段 + Win32 尾随字符剥离，**保留大小写**。

    ``./role.json`` 与 ``role.json`` 在**任何**文件系统上都是同一路径（``zip -r .`` 会带
    该前缀），所以路径层面必须视为同一个名字。

    **为什么尾随字符剥离放在这里、而不是只放在 :func:`_fold_name`**：
    ``_norm_path`` 同时服务两处判定 ——「包内是否含 role.json」（:func:`validate_zip`）
    与「读哪个成员」（:func:`read_member`）。若只让冲突检测剥离、存在性检查按原名判，
    就会出现「``role.json.`` 被判为『含 role.json』但 ``_fold_name`` 认为它与
    ``role.json`` 是两个文件」的不一致 —— 恰好是本条缺陷要消除的那类不一致。
    两处必须共用同一份归一化结果，否则修好一处、弄坏另一处。

    剥离后变空的段（``'...'`` / ``'..␠'`` / 纯空格段）直接丢弃：这与 Win32 落盘一致
    （这类段在 NTFS 上根本建不出独立目录），且方向是**向内收敛**—— 绝不可能把某段
    剥离成真的 ``..``。真正的 ``..`` 仍由 :func:`_check_name` 按**未剥离**的段拒绝，
    解包时还有 :func:`extract_safely` 的 ``resolve()`` 越界兜底（实测 ``'..␠/evil.json'``
    会被它拦成「越界写入被拒」）。
    """
    return "/".join(_norm_segments(name))


def _norm_segments(name: str) -> list[str]:
    """把成员名切成**已归一化**的路径段列表（:func:`_norm_path` 与 :func:`_fold_name` 共用）。

    抽成独立函数是为了让「存在性检查」与「冲突检测」在结构上共用同一步骤，
    而不是各自复制一份逻辑 —— 后者正是本条缺陷的成因（两处对同一个名字给出不同答案）。
    """
    segments: list[str] = []
    for raw in name.replace("\\", "/").split("/"):
        if raw in ("", "."):
            continue
        stripped = _win32_strip(raw)
        if stripped == "":
            # 整段都是空格/点：Win32 归一化后这一段并不存在
            continue
        segments.append(stripped)
    return segments


def _fold_name(name: str) -> str:
    """**冲突检测**键：路径归一化 + 大小写折叠。

    两类别名都必须被判为冲突，否则「校验读到的那一份」与「最后落盘的那一份」可以不是
    同一份内容：
    - ``role.json`` 与 ``./role.json``：任何文件系统上都是同一路径；
    - ``role.json`` 与 ``Role.json``：大小写不敏感的文件系统（Windows / macOS）上是同一文件；
    - ``role.json`` 与 ``role.json.`` / ``role.json␠``：**任何**文件系统上都是同一文件
      （Win32 会剥离尾随空格与点，实测三者落盘只剩一个文件、内容为最后写入者）。

    注意：大小写折叠**只**用于冲突检测。判定「是否含 role.json」用 ``_norm_path``
    （保留大小写）——否则单成员 ``Role.json`` 会在大小写不敏感系统上蒙混过关、
    在 Linux 上却又失败，造成平台行为不一致。
    """
    return _norm_path(name).casefold()


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
        # 按**未剥离**的段判`..`：Win32 归一化发生在落盘时，而这一层必须对着
        # 「zip 里写的原始名字」拒绝。剥离后 `..` 类段已被 `_norm_segments` 丢弃
        # （见那里的说明），方向是向内收敛，不会把某段变成真的 `..`。
        raise RolePackError(f"拒绝路径穿越: {name}")
    # 尾随空格/点必须**先剥离再判后缀**，否则 `evil.py.` 能绕过黑名单：实测它
    # 落盘后就是 `evil.py`（NTFS 剥离尾随点），即黑名单形同虚设。
    # 误伤面：合法动作文件一律 `.json`，不在黑名单内，故对真实内容零误伤。
    stripped = "/".join(_win32_strip(p) for p in parts)
    if stripped.lower().endswith(FORBIDDEN_SUFFIXES):
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
        # 按**归一化路径**（折叠 `.` 段、保留大小写）要求 role.json 存在：
        # - 只看「解压后目录里有 role.json」会被单成员 `Role.json` 蒙混过关（大小写不敏感的
        #   文件系统上 `incoming / "role.json"` 会命中它），而 Linux 上又会失败 → 平台不一致；
        # - 用大小写折叠判断则会反过来误拒 `Role.json`（见上）。
        if ROLE_JSON not in {_norm_path(info.filename) for info in files}:
            raise RolePackError(f"角色包缺少 {ROLE_JSON}")
        return infos


def read_member(zip_path: str | Path, name: str = ROLE_JSON) -> bytes:
    """在**不落盘**的前提下读取包内单个文件（先全量校验，防边读边炸）。

    用于导入前读出 ``role.json`` 以确定目标角色目录并做 schema 校验，
    避免「先替换再发现非法」导致现网角色被污染。

    按**归一化路径**匹配成员，使 ``./role.json`` / ``.\\role.json`` 这类合法前缀包也能读到
    （``zip -r .`` 会带该前缀；精确名匹配会把它们误判成「缺少 role.json」）。
    """
    validate_zip(zip_path)
    wanted = _norm_path(name)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if not info.is_dir() and _norm_path(info.filename) == wanted:
                return archive.read(info)
    raise RolePackError(f"角色包缺少 {name}")


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
            restored = False
            if backup.exists() and not target.exists():
                try:
                    backup.rename(target)
                    restored = True
                except OSError as restore_exc:
                    raise RolePackError(
                        f"角色包换入失败，且旧版本还原也失败：旧版本在 {backup}，"
                        f"目标位 {target} 当前缺失，需人工恢复。"
                        f"（换入错误: {exc}；还原错误: {restore_exc}）"
                    ) from exc
            # 统一转成 RolePackError：这是本模块对外承诺的失败类型，调用方（web 层）
            # 据此回 400 契约错误；抛裸 OSError 会漏成纯文本 500。
            raise RolePackError(
                f"角色包换入失败{'（旧版本已还原）' if restored else ''}: {exc}"
            ) from exc
        shutil.rmtree(backup, ignore_errors=True)
        return ExtractReport(files=report.files, total_bytes=report.total_bytes,
                             role_json=str(target / ROLE_JSON))
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
