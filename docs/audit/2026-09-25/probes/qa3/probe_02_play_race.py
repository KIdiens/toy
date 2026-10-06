"""Probe 02: 并发 /actions/play + /actions/stop + 切角色 交叉。

断言：并发 play 恰好 1 个 200，其余 409（不叠加时间轴）。
"""
from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402


def burst(srv, entries, timeout=15):
    """entries: list of (method, path, payload)"""
    barrier = threading.Barrier(len(entries))
    results = [None] * len(entries)

    def worker(i, method, path, payload):
        barrier.wait()
        try:
            results[i] = srv.json_req(method, path, payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            results[i] = ("EXC", repr(exc))

    ts = [threading.Thread(target=worker, args=(i, m, p, pl))
          for i, (m, p, pl) in enumerate(entries)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=timeout + 5)
    return results


srv = Server().start()
try:
    st, files = srv.json_req("GET", "/api/v1/actions/files")
    print("action files:", st, files)
    name = files["data"][0]["name"] if files["data"] else None
    print("use action:", name)

    # --- A: 纯并发 play（同一动作 30 份）---
    print("\n== A: 30 并发 play（同动作）==")
    entries = [("POST", "/api/v1/actions/play",
                {"request_id": f"p{i}", "role_id": 1, "action_id": name})
               for i in range(30)]
    res = burst(srv, entries)
    codes = {}
    for r in res:
        codes[r[0]] = codes.get(r[0], 0) + 1
    print("codes:", codes, "  <- 期望 1x200, 29x409")
    ok200 = codes.get(200, 0)
    print("200 count:", ok200, "PASS" if ok200 == 1 else "FAIL(叠加!)")

    time.sleep(1.5)
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("after-A:", snap["data"]["state"], "action:", snap["data"]["action"])

    # --- B: play × stop 交叉 ---
    print("\n== B: play/stop 交叉 20 路 ==")
    entries2 = []
    for i in range(20):
        if i % 2 == 0:
            entries2.append(("POST", "/api/v1/actions/play",
                             {"request_id": f"ps{i}", "role_id": 1, "action_id": name}))
        else:
            entries2.append(("POST", "/api/v1/actions/stop", {"request_id": f"st{i}"}))
    res2 = burst(srv, entries2)
    codes2 = {}
    for r in res2:
        codes2[r[0]] = codes2.get(r[0], 0) + 1
    print("codes:", codes2)
    time.sleep(1.0)
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("after-B:", snap["data"]["state"], "alive:", snap["data"]["worker_alive"],
          "faults:", snap["data"]["worker_faults"])

    # --- C: 切角色 × play × 按键 三路交叉 ---
    print("\n== C: 切角色×play×按键 三路交叉 24 路 ==")
    entries3 = []
    for i in range(24):
        m = i % 3
        if m == 0:
            entries3.append(("PATCH", "/api/v1/roles/active",
                             {"request_id": f"r{i}", "role_id": (i % 3) + 1}))
        elif m == 1:
            entries3.append(("POST", "/api/v1/actions/play",
                             {"request_id": f"p3{i}", "role_id": 1, "action_id": name}))
        else:
            entries3.append(("POST", "/api/v1/dev/button",
                             {"request_id": f"b3{i}", "n": 1}))
    res3 = burst(srv, entries3)
    codes3 = {}
    for r in res3:
        codes3[r[0]] = codes3.get(r[0], 0) + 1
    print("codes:", codes3)

    # 收敛观察
    print("\n== 收敛观察 ==")
    seen = []
    for _ in range(10):
        time.sleep(0.4)
        st, snap = srv.json_req("GET", "/api/v1/status")
        seen.append((snap["data"]["state"], snap["data"]["session_generation"],
                     snap["data"]["worker_alive"]))
    for s in seen:
        print("  ", s)
    print("final:", seen[-1])
    print("alive throughout?", all(x[2] for x in seen))

    # 一致性：/roles 与 status.role_id
    st, roles = srv.json_req("GET", "/api/v1/roles")
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("roles:", [r["role_id"] for r in roles["data"]],
          "status.role_id:", snap["data"]["role_id"])
finally:
    code = srv.stop()
    print("\nSERVER_EXIT", code)
