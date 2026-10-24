"""把静态扫描接入 pytest/CI，并验证坏输入确实返回失败。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests/frontend/selfcheck_static.py"


def scan(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True,
        encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        timeout=15, check=False,
    )


@pytest.fixture()
def clean_tree(tmp_path):
    static = tmp_path / "host/web/static"
    static.mkdir(parents=True)
    for name in ["index.html", "editor.html", "roles.html", "memories.html", "reminders.html",
                 "settings.html", "api.js", "app.js", "timeline.js", "styles.css",
                 "styles-editor.css"]:
        (static / name).write_text("", encoding="utf-8")
    (static / "api.js").write_text(
        "/status /roles /roles/active /persona /persona/preview /actions/validate "
        "/actions/play /actions/stop /content/import /memories /reminders "
        "/settings /features /events",
        encoding="utf-8",
    )
    (static / "app.js").write_text("ic('check')", encoding="utf-8")
    (static / "vendor").mkdir()
    (static / "vendor/lucide-icons.js").write_text('"check": 1,\n', encoding="utf-8")
    tokens = tmp_path / "host/web/design/design-tokens.css"
    tokens.parent.mkdir()
    tokens.write_text(":root { --color-text: #123456; }", encoding="utf-8")
    fixture = tmp_path / "tests/fixtures/lucide-1.47.0-icon-ids.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("check\n", encoding="utf-8")
    return tmp_path


def test_frontend_static_gate_is_clean():
    result = scan(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: PASS" in result.stdout


def test_known_good_tree_passes(clean_tree):
    result = scan(clean_tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_root_cannot_pass_as_an_empty_scan(tmp_path):
    result = scan(tmp_path / "not-present")
    assert result.returncode != 0 and "缺少必需文件" in result.stdout


@pytest.mark.parametrize("violation", [
    "line-limit", "emoji", "color", "gradient", "unknown-icon", "unpacked-icon",
    "missing-page", "missing-endpoint",
])
def test_static_violation_fails_instead_of_only_printing_warning(clean_tree, violation):
    static = clean_tree / "host/web/static"
    if violation == "line-limit":
        (static / "oversized.js").write_text("// line\n" * 301, encoding="utf-8")
    elif violation == "emoji":
        (static / "app.js").write_text("const icon = '\U0001f680';", encoding="utf-8")
    elif violation == "color":
        (static / "styles.css").write_text(".label { color: #123456; }", encoding="utf-8")
    elif violation == "gradient":
        (static / "styles.css").write_text(
            ".label { background: linear-gradient(purple, pink); }", encoding="utf-8",
        )
    elif violation == "unknown-icon":
        (static / "app.js").write_text("ic('not-in-fixture')", encoding="utf-8")
    elif violation == "unpacked-icon":
        (clean_tree / "tests/fixtures/lucide-1.47.0-icon-ids.txt").write_text(
            "check\nunpacked-icon\n", encoding="utf-8",
        )
        (static / "app.js").write_text("ic('unpacked-icon')", encoding="utf-8")
    elif violation == "missing-page":
        (static / "editor.html").unlink()
    else:
        p = static / "api.js"
        p.write_text(p.read_text(encoding="utf-8").replace("/settings", ""), encoding="utf-8")
    result = scan(clean_tree)
    assert result.returncode != 0, f"{violation} 被当作通过：\n{result.stdout}"
    assert "RESULT: FAIL" in result.stdout
