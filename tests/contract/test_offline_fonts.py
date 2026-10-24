"""离线字体必须存在、绑定原字体族并携带可核对的来源和许可。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "host/web/static/fonts"
EXPECTED = {
    "Figtree": "Figtree", "Lexend": "Lexend",
    "JetBrainsMono": "JetBrains Mono", "NotoSansSC": "Noto Sans SC",
}


@pytest.mark.parametrize("key,family", EXPECTED.items())
def test_font_binary_css_binding_license_and_provenance(key, family):
    manifest = json.loads((FONT_DIR / "sources.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[a-f0-9]{40}", manifest["commit"])
    assert {f["family"] for f in manifest["fonts"]} == set(EXPECTED)
    record = next(f for f in manifest["fonts"] if f["family"] == key)
    font = (FONT_DIR / record["file"]).read_bytes()
    assert font[:4] == b"wOF2", "必须是可解码的 WOFF2 资源，不能用空文件占位"
    assert int.from_bytes(font[8:12], "big") == len(font) == record["bytes"]
    assert int.from_bytes(font[12:14], "big") > 0, "字体必须包含实际表数据"
    assert hashlib.sha256(font).hexdigest() == record["sha256"]
    license_data = (FONT_DIR / record["license"]).read_bytes()
    assert b"Copyright" in license_data and b"SIL OPEN FONT LICENSE Version 1.1" in license_data
    assert hashlib.sha256(license_data).hexdigest() == record["license_sha256"]
    assert f"/{manifest['commit']}/ofl/" in record["source_url"]
    assert record["source_url"].startswith("https://raw.githubusercontent.com/google/fonts/")
    assert re.fullmatch(r"[a-f0-9]{64}", record["source_sha256"])
    css = (ROOT / "host/web/design/design-tokens.css").read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{([^}]+)\}", css)
    face = next(block for block in faces if f"font-family: '{family}'" in block)
    assert f"url('/static/fonts/{record['file']}')" in face
    assert "local(" not in face and "font-weight: 400 700" in face
    assert "font-display: swap" in face


def test_chinese_subset_contains_current_interface_text():
    manifest = json.loads((FONT_DIR / "sources.json").read_text(encoding="utf-8"))
    record = next(f for f in manifest["fonts"] if f["family"] == "NotoSansSC")
    coverage = set(record["codepoints"])
    for path in (ROOT / "host/web/static").rglob("*"):
        if path.suffix not in {".html", ".js", ".css"}:
            continue
        chinese = {ord(c) for c in path.read_text(encoding="utf-8") if 0x4E00 <= ord(c) <= 0x9FFF}
        assert chinese <= coverage, f"新增界面中文尚未加入离线子集：{path.name}"
    assert set(map(ord, "动作编排角色人设保存恢复默认0123456789")) <= coverage
