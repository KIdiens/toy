"""Probe 05: ≥10 分钟长时运行 —— 采样 RSS/句柄/线程数，持续驱动负载。

负载：每 2s GET /status；每 5s play+stop；每 12s 切角色。
关注新增 3 个领域线程后线程数是否稳定、反复动作后是否回落。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server, sample_proc, child_pids  # noqa: E402

DURATION_S = int(sys.argv[1]) if len(sys.argv) > 1 else 630
INTERVAL_SAMPLE = 15

srv = Server().start()
main_pid = srv.proc.pid
samples = []
http_5xx = 0
http_other = 0
counts = {"status": 0, "play": 0, "stop": 0, "role": 0, "button": 0}

st, files = srv.json_req("GET", "/api/v1/actions/files")
name = files["data"][0]["name"] if files["data"] else "nod"
st, roles = srv.json_req("GET", "/api/v1/roles")
role_ids = [r["role_id"] for r in roles["data"]] or [1]

t0 = time.time()
next_sample = t0
next_play = t0
next_role = t0
last_summary = None
try:
    while time.time() - t0 < DURATION_S:
        now = time.time()
        # 高频 status
        st, snap = srv.json_req("GET", "/api/v1/status")
        counts["status"] += 1
        if isinstance(st, int) and st >= 500:
            http_5xx += 1
        # play + stop
        if now >= next_play:
            s1, _ = srv.json_req("POST", "/api/v1/actions/play",
                                 {"request_id": f"lp-{int(now)}", "role_id": role_ids[0],
                                  "action_id": name})
            counts["play"] += 1
            if isinstance(s1, int) and s1 >= 500:
                http_5xx += 1
            s2, _ = srv.json_req("POST", "/api/v1/actions/stop", {"request_id": f"ls-{int(now)}"})
            counts["stop"] += 1
            if isinstance(s2, int) and s2 >= 500:
                http_5xx += 1
            next_play = now + 5
        # 切角色
        if now >= next_role:
            rid = role_ids[int((now - t0) / 12) % len(role_ids)]
            s3, _ = srv.json_req("PATCH", "/api/v1/roles/active",
                                 {"request_id": f"lr-{int(now)}", "role_id": rid})
            counts["role"] += 1
            next_role = now + 12
        # 采样
        if now >= next_sample:
            s = sample_proc(main_pid)
            s["t"] = round(now - t0, 1)
            samples.append(s)
            last_summary = s
            next_sample = now + INTERVAL_SAMPLE
        time.sleep(0.25)

    # 收尾统计
    rss = [s.get("rss_mb") for s in samples if "rss_mb" in s]
    hnd = [s.get("handles") for s in samples if s.get("handles") is not None]
    thr = [s.get("threads") for s in samples if s.get("threads") is not None]
    print("=== 长跑结束 ===")
    print("duration_s:", round(time.time() - t0, 1))
    print("samples:", len(samples))
    print("requests:", counts)
    print("http_5xx:", http_5xx)
    print("RSS  first/max/last:", rss[0], max(rss), rss[-1], "MB")
    print("HANDLE first/max/last:", hnd[0] if hnd else None, max(hnd) if hnd else None,
          hnd[-1] if hnd else None)
    print("THREAD first/max/last:", thr[0] if thr else None, max(thr) if thr else None,
          thr[-1] if thr else None)
    print("--- samples ---")
    for s in samples:
        print("  ", s)
    # 线程数后 1/3 是否稳定
    if thr:
        tail = thr[len(thr) // 3:]
        print("THREAD tail min/max:", min(tail), max(tail))
finally:
    code = srv.stop()
    print("SERVER_EXIT", code)
    time.sleep(0.5)
    print("child_pids after stop:", child_pids(main_pid))
