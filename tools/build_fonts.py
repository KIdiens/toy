"""从固定上游版本重建离线 WOFF2；下载缓存放在工程外，字体许可随产物保留。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "45e3d5269e4a17d823f6d84470bc084dc5ae46f6"
SOURCES = {
    "Figtree": ("figtree", "Figtree[wght].ttf"),
    "Lexend": ("lexend", "Lexend[wght].ttf"),
    "JetBrainsMono": ("jetbrainsmono", "JetBrainsMono[wght].ttf"),
    "NotoSansSC": ("notosanssc", "NotoSansSC[wght].ttf"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_url(directory: str, filename: str) -> str:
    path = urllib.parse.quote(f"ofl/{directory}/{filename}")
    return f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/{path}"


def cached_source(url: str, cache: Path, expected: str | None = None) -> bytes:
    target = cache / sha256(url.encode())
    if not target.is_file():
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(20_000_001)
        if len(data) > 20_000_000:
            raise ValueError(f"字体源文件超过 20 MB：{url}")
        if expected and sha256(data) != expected:
            raise ValueError(f"源文件哈希与已登记版本不符：{url}")
        target.write_bytes(data)
    data = target.read_bytes()
    if expected and sha256(data) != expected:
        raise ValueError(f"缓存源文件哈希不符：{url}")
    return data


def ui_codepoints() -> set[int]:
    """现有程序/内容的中文与固定标点；用户任意输入仍保留系统字体回退。"""
    points = set(range(0x20, 0x250)) | set(range(0x2000, 0x2070)) | set(range(0x3000, 0x3040))
    for directory in (ROOT / "host", ROOT / "content"):
        for path in sorted(directory.rglob("*")):
            if path.is_relative_to(ROOT / "host/web/static/fonts"):
                continue  # 已生成的来源清单不能反向成为取字输入。
            if path.suffix not in {".html", ".js", ".css", ".py", ".json", ".toml"}:
                continue
            points.update(ord(c) for c in path.read_text(encoding="utf-8")
                          if 0x3400 <= ord(c) <= 0x9FFF or 0xFF00 <= ord(c) <= 0xFFEF)
    return points


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path(tempfile.gettempdir()) / f"toy-font-sources-{COMMIT[:12]}")
    args = parser.parse_args()
    requirements = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["optional-dependencies"]["fonts"]
    for requirement in requirements:
        name, version = requirement.split("==")
        if importlib.metadata.version(name) != version:
            raise RuntimeError('请先安装 pyproject.toml 的字体构建依赖：pip install -e ".[fonts]"')

    from fontTools import subset
    from fontTools.ttLib import TTFont

    output = ROOT / "host/web/static/fonts"
    output.mkdir(exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "sources.json"
    previous = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    old = {entry["family"]: entry for entry in previous.get("fonts", [])}
    points = ui_codepoints()
    records = []
    for family, (directory, filename) in SOURCES.items():
        entry = old.get(family, {})
        url = source_url(directory, filename)
        source = cached_source(url, args.cache, entry.get("source_sha256"))
        license_url = source_url(directory, "OFL.txt")
        license_data = cached_source(license_url, args.cache, entry.get("license_sha256"))
        if b"SIL OPEN FONT LICENSE Version 1.1" not in license_data:
            raise ValueError(f"源许可不是已核实的 OFL 1.1：{family}")
        font = TTFont(io.BytesIO(source), recalcTimestamp=False)
        if family == "NotoSansSC":
            options = subset.Options()
            options.recalc_timestamp = False
            options.name_IDs = ["*"]
            sub = subset.Subsetter(options=options)
            sub.populate(unicodes=points & set(font.getBestCmap()))
            sub.subset(font)
        coverage = sorted(font.getBestCmap())
        font.flavor = "woff2"
        destination = output / f"{family}-Variable.woff2"
        font.save(destination, reorderTables=True)
        font.close()
        license_name = f"{family}-OFL.txt"
        (output / license_name).write_bytes(license_data)
        records.append({
            "family": family, "file": destination.name, "license": license_name,
            "source_url": url, "source_sha256": sha256(source),
            "license_url": license_url, "license_sha256": sha256(license_data),
            "sha256": sha256(destination.read_bytes()), "bytes": destination.stat().st_size,
            "codepoints": coverage,
        })
        print(f"{destination.name}: {destination.stat().st_size} bytes, {len(coverage)} codepoints")
    manifest_path.write_text(json.dumps({
        "upstream": "https://github.com/google/fonts", "commit": COMMIT,
        "build_requirements": requirements, "fonts": records,
        "coverage_policy": "拉丁字体保留完整字集；"
                           "Noto Sans SC 为当前 host/content 中文及标点子集。",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
