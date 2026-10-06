"""Probe 01: 并发按键风暴 —— 同一毫秒窗口内多次 POST /dev/button。

观察：状态机是否出现非法序列/卡死、session_generation 是否错乱、
      worker_alive/worker_faults 是否异常。
"""
from __future__ import annotations

import json
import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402

VALID_STATES = {"SELF_TEST", "IDLE", "RECORDING", "THINKING", "SPEAKING",
                "PERFORMING", "FAULT_LOCKED"}


def storm(srv: Server, n_threads: int, button: int) -> tuple[list, list]:
    barrier = threading.Barrier(n_threads)
    results: list = [None] * n_threads
    errors: list = [None] * n_threads

    def worker(i: int) -> None:
        barrier.wait()
        try:
            st, d = srv.json_req("POST", "/api/v1/dev/button",
                                 {"request_id": f"storm-{button}-{i}", "n": button},
                                 timeout=15)
            results[i] = (st, d.get("data") if isinstance(d, dict) else d)
        except Exception as exc:  # noqa: BLE001
            errors[i] = repr(exc)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    return results, errors


def summarize(results, errors):
    codes = {}
    for r in results:
        if r is None:
            continue
        codes[r[0]] = codes.get(r[0], 0) + 1
    return codes, [e for e in errors if e]


srv = Server().start()
try:
    print("== baseline ==")
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("status", st, snap["data"])

    # --- 风暴 A：40 并发按对话键（1）---
    print("\n== 风暴A：40 并发按键 n=1 ==")
    res, errs = storm(srv, 40, 1)
    codes, e = summarize(res, errs)
    print("HTTP codes:", codes, "errors:", e)
    time.sleep(0.5)
    st, snap = srv.json_req("GET", "/api/v1/status")
    d = snap["data"]
    print("after-A state:", d["state"], "gen:", d["session_generation"],
          "alive:", d["worker_alive"], "faults:", d["worker_faults"])
    print("state legal?", d["state"] in VALID_STATES)

    # --- 风暴 B：30 并发混合按键（1/2/3 交错）---
    print("\n== 风暴B：30 并发混合按键 1/2/3 ==")
    res2, errs2 = storm(srv, 30, 2)  # perform key
    codes2, e2 = summarize(res2, errs2)
    print("perform codes:", codes2, "errors:", e2)
    time.sleep(0.5)
    st, snap = srv.json_req("GET", "/api/v1/status")
    d = snap["data"]
    print("after-B state:", d["state"], "gen:", d["session_generation"],
          "alive:", d["worker_alive"], "faults:", d["worker_faults"])

    # --- 收敛性：连续 poll 20 次看是否卡死 ---
    print("\n== 收敛观察（每 0.3s 一次，共 5s）==")
    seen = []
    for _ in range(17):
        time.sleep(0.3)
        st, snap = srv.json_req("GET", "/api/v1/status")
        seen.append(snap["data"]["state"])
    print("states:", seen)
    print("final:", seen[-1], "stuck(RECORDING/THINKING at end)?", seen[-1] in ("RECORDING", "THINKING"))

    # --- 故障注入后再风暴，确认不崩 ---
    print("\n== 故障锁定 + 按键风暴 ==")
    srv.json_req("POST", "/api/v1/dev/fault", {"request_id": "f1", "code": "SAFETY_TRIP"})
    time.sleep(0.3)
    res3, errs3 = storm(srv, 20, 1)
    codes3, _ = summarize(res3, errs3)
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("fault-locked codes:", codes3, "state:", snap["data"]["state"])

    print("\nworker log faults lines:")
    for ln in srv.log_text().splitlines():
        if "faults" in ln.lower() or "Traceback" in ln or "ERROR" in ln:
            print("  ", ln)
finally:
    code = srv.stop()
    print("\nSERVER_EXIT", code)
