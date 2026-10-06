"""Probe 04: net worker 被占住时，核心/按键仍即时响应（AC-22）。

进程内装配：把 ctx.router.respond_audio 换成 8s 慢响应，模拟 10s 级假 LLM。
在 net worker 忙等期间，测量 status_snapshot() 与 press_button() 的耗时。
"""
from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import REPO  # noqa: E402

from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402

SLOW_S = 8.0

settings = load_settings({"DB_PATH": str(REPO / "data" / "_qa3_netbusy.sqlite3"),
                          "AI_PROVIDER": "stub"})
ctx = build_context(settings, simulate=True, root=REPO)

# 注入慢响应
orig = ctx.router.respond_audio

def slow_respond(wav):
    t = time.time()
    time.sleep(SLOW_S)
    print(f"    [slow net] respond_audio 完成，耗时 {time.time()-t:.1f}s")
    return orig(wav)

ctx.router.respond_audio = slow_respond

stop = threading.Event()
ctx.worker_running = True
ctx.start_workers()
worker = threading.Thread(target=ctx.run_forever, args=(stop,), daemon=True)
worker.start()
time.sleep(0.5)
print("boot state:", ctx.state_machine.state.value,
      "worker_alive:", ctx.worker_alive())

# 进入 THINKING：按键1两次
ctx.press_button(1)
time.sleep(0.3)
ctx.press_button(1)
time.sleep(0.3)
print("after 2 presses, state:", ctx.state_machine.state.value,
      "(net worker 应正在慢响应)")

# 在 net 忙等期间测量核心响应
lat_status = []
lat_press = []
for i in range(12):
    t = time.time(); ctx.status_snapshot(); lat_status.append((time.time() - t) * 1000)
    t = time.time(); ctx.press_button(3); lat_press.append((time.time() - t) * 1000)  # 音量键
    time.sleep(0.4)

print(f"status_snapshot 延迟 ms: max={max(lat_status):.2f} avg={sum(lat_status)/len(lat_status):.2f}")
print(f"press_button(3) 延迟 ms: max={max(lat_press):.2f} avg={sum(lat_press)/len(lat_press):.2f}")
print("state during load:", ctx.state_machine.state.value)

# 再看门锁是否被 net 持住：模拟取消（按键1 → IDLE）
t = time.time(); ctx.press_button(1); dt = (time.time() - t) * 1000
time.sleep(0.3)
print(f"cancel press(1) 耗时 {dt:.2f}ms, state now:", ctx.state_machine.state.value)

# 等慢响应结束
time.sleep(SLOW_S + 1.0)
print("final state:", ctx.state_machine.state.value,
      "worker_faults:", ctx.worker_faults,
      "alive:", ctx.worker_alive())

stop.set()
ctx.close()
time.sleep(0.3)
print("threads after close:", threading.active_count(),
      [t.name for t in threading.enumerate()])
