"""Probe 14: 领域线程静默死亡的可见性 —— worker_alive 是否覆盖 net/audio/tick。

进程内：让 net 线程因未捕获异常退出（_net_loop 无外层 try/except），
观察 worker_alive / worker_faults / /status 是否能反映。
"""
from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import REPO  # noqa: E402

from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402

settings = load_settings({"DB_PATH": str(REPO / "data" / "_qa3_liveness.sqlite3"),
                          "AI_PROVIDER": "stub"})
ctx = build_context(settings, simulate=True, root=REPO)


def names():
    return sorted(t.name for t in threading.enumerate())


# 注入：让 net 领域处理器抛未捕获异常（模拟领域线程内部 bug）
orig_handle = ctx._handle_net_request
boom = {"n": 0}


def raising_handle(command):
    boom["n"] += 1
    raise RuntimeError("[probe] 模拟 net 领域处理器崩溃")


ctx._handle_net_request = raising_handle

stop = threading.Event()
ctx.worker_running = True
ctx.start_workers()
w = threading.Thread(target=ctx.run_forever, args=(stop,), daemon=True)
w.start()
time.sleep(0.5)
print("启动后线程:", names())
print("worker_alive:", ctx.worker_alive(), "worker_faults:", ctx.worker_faults)

# 触发一次对话 → THINKING → 下发 NET_REQUEST → net 线程处理时崩溃
ctx.press_button(1); time.sleep(0.3)
ctx.press_button(1); time.sleep(0.5)
print("\n触发 NET_REQUEST 后：")
print("  net 处理器被调用次数:", boom["n"])
print("  仍在运行的 host-* 线程:", [n for n in names() if n.startswith("host-")])
print("  worker_alive:", ctx.worker_alive(), " (net 线程已死，但心跳是 motion worker 的)")
print("  worker_faults:", ctx.worker_faults, " (未因 net 线程死亡而增加)")
print("  status.state:", ctx.state_machine.state.value)

# 再触发一次：NET_REQUEST 无人消费 → 状态机是否卡住
ctx.press_button(1); time.sleep(0.5)  # THINKING cancel -> IDLE
ctx.press_button(1); time.sleep(0.3)
ctx.press_button(1); time.sleep(1.0)
print("\n再次对话后 state:", ctx.state_machine.state.value,
      "net 处理器调用次数仍为:", boom["n"])
print("→ 结论：domain 线程（net）静默死亡后，worker_alive 仍为 True、",
      "worker_faults 不变、state 卡 THINKING（仅靠 60s tick 兜底）。")

stop.set(); ctx.close(); time.sleep(0.3)
print("close 后线程:", names())
