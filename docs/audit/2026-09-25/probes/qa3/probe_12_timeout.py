"""Probe 12: 时钟/超时 —— (A) 录音 15s 自动收尾是否真会触发；
(B) THINKING 最坏驻留时长（换角色丢弃 NET_REQUEST → 靠 tick 60s 兜底）。
"""
from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server, REPO  # noqa: E402

print("========== A) 录音自动收尾（NullBackend）==========")
srv = Server().start()
try:
    srv.json_req("POST", "/api/v1/dev/button", {"request_id": "a1", "n": 1})
    print("after press1:", srv.json_req("GET", "/api/v1/status")[1]["data"]["state"])
    states = []
    for i in range(80):  # 最多 24s
        time.sleep(0.3)
        st, snap = srv.json_req("GET", "/api/v1/status")
        states.append(snap["data"]["state"])
        if snap["data"]["state"] != "RECORDING":
            print(f"  {i*0.3:.1f}s 离开 RECORDING -> {snap['data']['state']}")
            break
    else:
        print("  ❌ 24s 内一直停在 RECORDING（15s 自动收尾未触发）")
    print("  最终:", states[-1], " 时长≈", len(states) * 0.3, "s")
finally:
    srv.stop()

print("\n========== B) THINKING 最坏驻留（进程内，实测真实 60s 常量）==========")
from host.adapters.runtime import build_context  # noqa: E402
from host.adapters import workers as W  # noqa: E402
from host.config.settings import load_settings  # noqa: E402

print("NET_THINKING_TIMEOUT_S =", W.NET_THINKING_TIMEOUT_S)
settings = load_settings({"DB_PATH": str(REPO / "data" / "_qa3_timeout.sqlite3"),
                          "AI_PROVIDER": "stub"})
ctx = build_context(settings, simulate=True, root=REPO)

# 让 net worker 一直忙（模拟真实 LLM 慢），随后切角色丢弃其回包
def endless(wav):
    time.sleep(120)
    return None
ctx.router.respond_audio = endless

stop = threading.Event()
ctx.worker_running = True
ctx.start_workers()
w = threading.Thread(target=ctx.run_forever, args=(stop,), daemon=True)
w.start()
time.sleep(0.5)

ctx.press_button(1); time.sleep(0.3)
ctx.press_button(1); time.sleep(0.3)
print("进入:", ctx.state_machine.state.value, "gen=", ctx.state_machine.generation)

# 换角色：THINKING 允许 → gen+1 → net worker 回包作废
ctx.switch_active_role(2)
print("换角色后 gen=", ctx.state_machine.generation, "state=", ctx.state_machine.state.value)

t0 = time.time()
deadline = t0 + 75
while time.time() < deadline:
    if ctx.state_machine.state.value != "THINKING":
        break
    time.sleep(0.5)
dt = time.time() - t0
print(f"THINKING 驻留实测: {dt:.1f}s -> 终态 {ctx.state_machine.state.value}")
print("（预期 ≈ NET_THINKING_TIMEOUT_S=60s + tick 采样粒度）")

stop.set(); ctx.close(); time.sleep(0.3)
