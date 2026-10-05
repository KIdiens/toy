"""把tests/frontend/ 下的 Node 回归脚本纳入 `python -m pytest` 单命令口径。

**为什么需要这个包装器**
`tests/frontend/*.mjs` 是纯前端回归测试（自带最小 DOM 桩，不依赖真实浏览器），
但 pytest 默认只收集 `test_*.py` / `*_test.py`，因此这些脚本长期零覆盖 ——
`python -m pytest tests/frontend` 实测 EXIT=5（零收集）。本模块用`subprocess`
调`node` 执行它们，使 1132 个 Python 测试之外的前端断言也进入同一份报告。

**为什么 node 缺失时 fail 而不是 skip**
本仓库 ADR-007 明确「前端无 Node 构建链」，产品运行期确实不需要 Node；但
`tests/frontend/*.mjs`的断言强度依赖 Node 解释器。若 node 缺失时 skip，
`python -m pytest` 会「全绿」而这 400+ 行前端断言一行未跑 —— 正是本任务要
消灭的零覆盖假绿。（实测：改用 skipif 标记时，缺 node 会得到 "ss." / EXIT=0，
即静默假绿；改为在用例内fail 才真正转红。）
确有理由在无 Node 环境跑测试时，显式设置 `TOY_ALLOW_NO_NODE_TESTS=1`
承认这一取舍 —— 该开关会把每个用例标成 skipped，在报告里显式可见，
不是默认兜底。

**失败可读性**：每个 .mjs 一个独立用例（parametrize），断言消息里带文件名与
完整 stdout/stderr，node 的调用栈原样保留，不做二次包装丢信息。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# tests/frontend/verify_logic_and_render.mjs 的组件清单在脚本内维护（mods 数组），
# 新增前端组件只需改 .mjs，本文件无需同步 —— 这是选择 parametrize 单一真源的原因。
FRONTEND_DIR = Path(__file__).resolve().parent

#逐个文件独立成例：失败时 pytest 报告直接点名是哪个 .mjs 挂了。
SCRIPTS = sorted(FRONTEND_DIR.glob("verify_*.mjs"))

#: 超时上限（秒）。两个脚本本机实测各 <2s（batch2≈0.3s / logic≈0.8s），
#: 60s 足够宽松，同时避免 CI 上挂死的 node 进程无限占job。
TIMEOUT_S = 60

#: 允许在缺 Node 环境运行的显式环境变量名（默认关闭 = 缺 Node 即 fail）。
ALLOW_NO_NODE_ENV = "TOY_ALLOW_NO_NODE_TESTS"


def _node_executable() -> str | None:
    """返回可用的 node 可执行文件名，找不到则 None。

    只认 ``node``（不含 ``.exe``）：``shutil.which`` 已按平台自动解析
    ``node.exe``，写死扩展名反而会在 Linux 上失效。
    """
    return shutil.which("node")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_frontend_js(script: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """执行一个前端回归脚本，失败时把 node 输出原样带进断言消息。

    .mjs 内部用``import.meta.url`` 推导静态资源路径，与 cwd 无关
    （已实测从 /tmp 调用仍通过），故统一以脚本所在目录为 cwd 运行，与 CI 一致。

    缺 node 时**直接 fail 而非 skip**：skip 会让 `python -m pytest` 在零覆盖
    状态下全绿（本bug 曾真实发生过——skipif 标记实测得到 "ss." / EXIT=0）。
    """
    node = _node_executable()
    if node is None:
        if os.environ.get(ALLOW_NO_NODE_ENV):
            pytest.skip(
                f"未找到 node，但已显式设 {ALLOW_NO_NODE_ENV}=1 接受"
                f"{script.name} 不被执行（该脚本的前端断言本轮未覆盖）。"
            )
        pytest.fail(
            f"未找到 node 可执行文件，无法运行 {script.name} —— "
            f"{FRONTEND_DIR.name}/ 下的前端回归断言本轮完全未执行，"
            f"不得以skip 形式放过（那会让门禁假绿）。\n"
            f"修复：安装 Node（CI 见 .github/workflows/ci.yml 的 actions/setup-node），"
            f"或在本就无需跑前端断言的环境显式设 {ALLOW_NO_NODE_ENV}=1。",
            pytrace=False,
        )

    try:
        result = subprocess.run(  # noqa: S603 - 固定 node + 实测存在的脚本路径，无 shell
            [node, script.name],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # 挂死（而非断言失败）要有独立说法，便于直接定位是死锁/死循环。
        partial = exc.stdout or ""
        if isinstance(partial, bytes):  # text=False 时为 bytes
            partial = partial.decode("utf-8", errors="replace")
        pytest.fail(
            f"前端回归脚本 {script.name} 超过 {TIMEOUT_S}s 未结束（疑似死循环/死锁）。"
            f"超时前的输出：\n{partial.strip() or '（无）'}",
            pytrace=False,
        )

    if result.returncode != 0:
        #stdout 含每条 PASS/FAIL 明细，stderr 含 node 调用栈，两者都要。
        report = "\n".join(
            part
            for part in (
                f"--- stdout ---\n{result.stdout.strip()}",
                f"--- stderr ---\n{result.stderr.strip()}",
            )
            if part.split("---", 1)[-1].strip()
        )
        # 脚本存在但被误删/改名时给出区分性提示，避免与「断言挂了」混淆。
        pytest.fail(
            f"前端回归脚本 {script.name} 退出码 {result.returncode}（期望 0）：\n{report}",
            pytrace=False,
        )

    # 顺带把明细打进 pytest 的输出缓冲，便于 `-s` 下直接看到 200+ 行断言明细。
    with capsys.disabled():
        print(f"\n[node] {script.name} 全部通过：\n{result.stdout.rstrip()}")


def test_frontend_js_scripts_present() -> None:
    """守住「脚本被误删 → 上面的 parametrize 悄悄变成空集」这一退化。

    pytest 对空 parametrize 集合默认给 SKIPPED 而非 FAILED，若不设此哨兵，
    有人清空/改名 verify_*.mjs 时 CI 仍会全绿。
    """
    assert SCRIPTS, (
        f"{FRONTEND_DIR} 下未发现任何 verify_*.mjs；前端回归测试已被删除或改名，"
        f"门禁会退化为空转。请确认这是有意为之。"
    )