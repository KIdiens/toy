"""Probe 00: 冒烟 —— 验证探针护栏可用（起服务 / status / 一次按键闭环）。"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402

srv = Server().start()
try:
    st, data = srv.json_req("GET", "/api/v1/status")
    print("STATUS", st, data)
    # 按键 1：IDLE -> RECORDING
    st, d = srv.json_req("POST", "/api/v1/dev/button",
                         {"request_id": "smoke-1", "n": 1})
    print("PRESS1", st, d)
    time.sleep(0.2)
    print("AFTER1", srv.json_req("GET", "/api/v1/status")[1])
    # 再按 1：结束录音 -> THINKING -> (stub) -> SPEAKING
    st, d = srv.json_req("POST", "/api/v1/dev/button",
                         {"request_id": "smoke-2", "n": 1})
    print("PRESS2", st, d)
    for i in range(20):
        time.sleep(0.2)
        st, snap = srv.json_req("GET", "/api/v1/status")
        print(f"t={i*0.2:.1f}s state={snap['data']['state']} "
              f"gen={snap['data']['session_generation']} "
              f"alive={snap['data']['worker_alive']} faults={snap['data']['worker_faults']}")
        if snap["data"]["state"] == "IDLE":
            break
finally:
    code = srv.stop()
    print("SERVER_EXIT", code)
    print("--- server log tail ---")
    print(srv.log_text()[-1500:])
