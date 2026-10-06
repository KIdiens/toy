"""探针 G5-2：``host/main.py`` 启动期异常裸奔。

在**真子进程**里跑 ``python -m host.main``，抓真实 stdout/stderr/退出码，
证明「配置坏了时用户只能看到裸 traceback，没有一行中文说明」。

每种故障都用环境变量触发（不改仓库文件）：

===  ==========================  ==========================================
编号  故障                触发点
===  ==========================  ==========================================
A     HOST_PORT=not_a_port      ``load_settings`` 的 ``int()``
B     DB_PATH=指向一个目录       ``build_context`` 里的 ``sqlite3.connect``
C     MOTION_BAUD=fast          ``load_settings`` 的 ``int()``
D     LOG_LEVEL=VERBOSE         ``logging.basicConfig``（在 :57，比 A/C 更晚）
E     defaults.toml 语法错误    ``load_settings`` 的 ``tomllib.loads``
===  ==========================  ==========================================

D 单独说明：它证明 ``basicConfig`` 本身也是一处未捕获的启动期风险 ——
用户把level 拼错，崩在日志配置这一步，比 A/C 还早看到「日志没配好」。

**修复前后对照**（同一份探针，跑两遍即可对比）：

- 修复前：A/C/D/E 退出码均为 **1**（无法区分配置错与运行时崩），
  末行是 ``ValueError: invalid literal for int() ...`` 这类裸英文异常名。
- 修复后：配置类（A/C/D/E）退出码 **2**，运行时类（B）退出码 **3**，
  末行变成一行中文（``启动失败[配置加载]：...请检查 HOST_PORT ...``）。

用法::

    python docs/audit/2026-10-07-batches/probes/g5/probe_main_startup.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
TIMEOUT_S = 90


def _check_repo_root() -> None:
    """护栏：本探针靠 ``cwd=REPO_ROOT`` 让 ``-m host.main`` 能找到包。

    层级算错时子进程会以 ``No module named 'host'`` 失败 —— 那种输出看起来
    也像「裸 traceback」，会让探针得出完全错误的结论。故开工先断言。
    """
    if not (REPO_ROOT / "host" / "main.py").is_file():
        raise SystemExit(
            f"REPO_ROOT 算错了：{REPO_ROOT} 下没有 host/main.py（应为 parents[5]）"
        )


def _run(env_overrides: dict[str, str], *, port_free: bool = True) -> tuple[int, str]:
    """起真子进程跑 ``-m host.main``，返回 (退出码, stderr 尾部)。"""
    env = dict(os.environ)
    env.update(env_overrides)
    #给每个场景独立端口，避免并行/残留占用干扰结论。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        env.setdefault("HOST_PORT", str(probe.getsockname()[1]))
    proc = subprocess.run(
        [sys.executable, "-m", "host.main", "--simulate"],
        env=env, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=REPO_ROOT,
    )
    return proc.returncode, proc.stderr


def _case(label: str, env_overrides: dict[str, str], *, expect_fail: bool = True) -> None:
    print("=" * 72)
    print(f"{label}env={env_overrides}")
    print("=" * 72)
    try:
        code, err = _run(env_overrides)
    except subprocess.TimeoutExpired:
        print("!! 超时：进程真的起来了（说明该故障没触发，或服务已监听）")
        print()
        return
    tail = [ln for ln in err.strip().splitlines() if ln.strip()][-4:]
    print("退出码:", code)
    print("stderr 尾部:")
    for ln in tail:
        print("   ", ln)
    # 护栏：模块都找不到时，本场景的输出与「启动期裸奔」无关，不能拿来下结论。
    if "No module named 'host'" in err:
        raise SystemExit("REPO_ROOT 不对：子进程没找到 host 包，探针失效")
    # 关键判据：**末行**有没有面向用户的中文说明。
    # 只看末行，不能扫全部 tail —— 仓库路径「AI智能底座玩具」本身含中文，
    # 扫全文会把「路径里的中文」误判成「给用户的中文指引」（第一版就踩了这个坑）。
    last = tail[-1] if tail else ""
    has_cn_hint = any("一" <= ch <= "鿿" for ch in last)
    print(f"末行: {last!r}")
    print(f"→ 末行是裸 traceback/异常名（非中文指引）: {not has_cn_hint}")
    if expect_fail and code == 0:
        print("!! 退出码为 0 —— 启动失败却被当成成功（对 systemd 是灾难）")
    print()


def _case_port_taken() -> None:
    """端口已被占用：uvicorn 绑定失败。"""
    print("=" * 72)
    print("F. 端口已被占用（绑定失败）")
    print("=" * 72)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        code, err = _run({"HOST_PORT": str(port)})
    print("退出码:", code)
    for ln in [x for x in err.strip().splitlines() if x.strip()][-3:]:
        print("   ", ln)
    print("→ 注意：日志已经配好（有中文 INFO 行），但『就绪』先于绑定成功打印。")
    print()


def _case_toml_broken() -> None:
    """临时改坏 defaults.toml 再复原（必须复原成字节一致）。"""
    print("=" * 72)
    print("E. defaults.toml 语法错误（临时改坏，跑完复原）")
    print("=" * 72)
    src = REPO_ROOT / "host" / "config" / "defaults.toml"
    backup = src.read_bytes()
    try:
        src.write_bytes(b'[web\nhost = "1.2.3.4"\n')
        code, err = _run({})
    finally:
        src.write_bytes(backup)
    assert src.read_bytes() == backup, "defaults.toml 未复原！"
    print("已复原 defaults.toml（字节校验通过）")
    print("退出码:", code)
    for ln in [x for x in err.strip().splitlines() if x.strip()][-3:]:
        print("   ", ln)
    print()


def _case_healthy() -> None:
    """对照组：正常启动必须能起来（并立刻收尾，绝不留后台服务）。"""
    print("=" * 72)
    print("G. 对照组：不设任何故障 → 应正常启动（随后主动结束）")
    print("=" * 72)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ)
    env["HOST_PORT"] = str(port)
    env["DB_PATH"] = str(Path(tempfile.gettempdir()) / "g5_probe.sqlite3")
    proc = subprocess.Popen(
        [sys.executable, "-m", "host.main", "--simulate"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=REPO_ROOT,
    )
    ready = False
    deadline = time.time() + 40
    while time.time() < deadline and proc.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.2)
    print("服务在超时前可连接:", ready)
    proc.terminate()  # 立刻收尾，绝不留后台进程
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    print(f"已终止子进程，returncode={proc.returncode}")
    print("→ 对照组证明上面的失败确实是故障触发的，而非环境本来就不通。\n")


if __name__ == "__main__":
    _check_repo_root()
    _case("A. HOST_PORT 非法（load_settings 的 int()）", {"HOST_PORT": "not_a_port"})
    _case("C. MOTION_BAUD 非法（load_settings 的 int()）", {"MOTION_BAUD": "fast"})
    _case("B. DB_PATH 指向目录（build_context 的 sqlite3.connect）",
          {"DB_PATH": str(Path(tempfile.gettempdir())), "HOST_PORT": "0"})
    _case("D. LOG_LEVEL 非法（logging.basicConfig 自身抛）", {"LOG_LEVEL": "VERBOSE"})
    _case_toml_broken()
    _case_port_taken()
    _case_healthy()