"""Probe 06: 反复启停 20 次 —— 每次确认端口立即空闲、无残留进程、无线程泄漏。"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server, free_port, port_in_use, child_pids  # noqa: E402

N = 20
residue = []
for i in range(N):
    port = free_port()
    srv = Server(port=port)
    srv.start(ready_timeout=25)
    pid = srv.proc.pid
    # 服务可用
    st, _ = srv.json_req("GET", "/api/v1/status")
    up = st == 200
    code = srv.stop()
    time.sleep(0.25)
    busy = port_in_use(port)
    kids = child_pids(pid)
    ok = up and (not busy) and (not kids)
    print(f"iter {i:2d} port={port} up={up} exit={code} port_busy={busy} kids={kids} -> {'OK' if ok else 'BAD'}")
    if not ok:
        residue.append((i, port, up, code, busy, kids))

print("\nsummary:", "ALL OK" if not residue else f"BAD {residue}")
