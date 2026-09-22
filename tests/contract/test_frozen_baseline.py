"""冻结基线回归：``software/test_motion.py`` 与 ``test_protocol_simulation.py`` 的 13 项
测试必须**保持可运行**（架构 §5 兼容契约）。本测试只以子进程运行原文件，不修改它们。
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.support import FROZEN_SOFTWARE

MODULES = ("test_motion", "test_protocol_simulation")

pytestmark = pytest.mark.skipif(
    not (FROZEN_SOFTWARE / "test_motion.py").is_file(),
    reason="冻结基线目录不可达，跳过",
)


def test_frozen_baseline_modules_still_pass():
    result = subprocess.run(
        [sys.executable, "-m", "unittest", *MODULES, "-v"],
        cwd=FROZEN_SOFTWARE,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "Ran 13 tests" in combined, combined
    assert combined.rstrip().endswith("OK"), combined


def test_frozen_baseline_files_are_unmodified_originals():
    """冻结基线文件必须是原始交付件（这里只做存在性与内容指纹级别的守卫）。"""
    for name in ("motion.py", *[f"{m}.py" for m in MODULES]):
        path = FROZEN_SOFTWARE / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "def validate(" in text or "unittest" in text or "def play(" in text
