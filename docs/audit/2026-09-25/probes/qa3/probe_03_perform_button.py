"""Probe 03: 孤立按键路径 —— 表演键(2) / 音量键(3) 的单次按行为。

怀疑：按键触发的 PERFORM_PRESS 走 _deliver 直接下发 PLAY_ACTION，
但未置 motion_busy；worker 的 pump_once 见到 motion_busy=False 会
把该 PLAY_ACTION 当作「陈旧命令」丢弃 → 不发 PERFORM_DONE → 卡在 PERFORMING。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402


def poll(srv, n, dt=0.3):
    out = []
    for _ in range(n):
        time.sleep(dt)
        st, snap = srv.json_req("GET", "/api/v1/status")
        out.append((snap["data"]["state"], snap["data"]["session_generation"],
                    snap["data"]["worker_alive"], snap["data"]["worker_faults"]))
    return out


# ---------- 单次表演键 ----------
print("========== 单次表演键 (n=2) ==========")
srv = Server().start()
try:
    print("start:", srv.json_req("GET", "/api/v1/status")[1]["data"]["state"])
    st, d = srv.json_req("POST", "/api/v1/dev/button", {"request_id": "perf1", "n": 2})
    print("press2 ->", st, d)
    obs = poll(srv, 10)
    for i, o in enumerate(obs):
        print(f"  +{(i+1)*0.3:.1f}s {o}")
    print("FINAL:", obs[-1][0],
          "<-- 若长期停在 PERFORMING 即为卡死")
finally:
    srv.stop()

# ---------- 单次音量键 ----------
print("\n========== 单次音量键 (n=3) ==========")
srv2 = Server().start()
try:
    st, d = srv2.json_req("POST", "/api/v1/dev/button", {"request_id": "vol1", "n": 3})
    print("press3 ->", st, d)
    obs = poll(srv2, 5)
    for i, o in enumerate(obs):
        print(f"  +{(i+1)*0.3:.1f}s {o}")
    print("FINAL:", obs[-1][0])
finally:
    srv2.stop()

# ---------- 表演键后再按一次（看能否自救）----------
print("\n========== 表演键(2) → 再按一次(2) ==========")
srv3 = Server().start()
try:
    srv3.json_req("POST", "/api/v1/dev/button", {"request_id": "a", "n": 2})
    time.sleep(1.0)
    st, snap = srv3.json_req("GET", "/api/v1/status")
    print("after 1st press2:", snap["data"]["state"])
    srv3.json_req("POST", "/api/v1/dev/button", {"request_id": "b", "n": 2})
    time.sleep(1.0)
    st, snap = srv3.json_req("GET", "/api/v1/status")
    print("after 2nd press2:", snap["data"]["state"])
    print("worker_faults:", snap["data"]["worker_faults"])
    print("--- log tail ---")
    print(srv3.log_text()[-1200:])
finally:
    srv3.stop()
