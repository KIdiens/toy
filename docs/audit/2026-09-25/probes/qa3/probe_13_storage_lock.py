"""Probe 13: 存储层锁 —— 外部 BEGIN EXCLUSIVE 期间发写请求。

关注：transaction() 的 BEGIN IMMEDIATE 在 try 之外 → sqlite3.OperationalError
是否被 translate() 成 StorageError，还是以裸异常 500（破坏统一信封）。
"""
from __future__ import annotations

import sqlite3
import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402

srv = Server().start()
try:
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("baseline:", st, snap["data"]["state"])

    # 正常创建一条，确认基线可用
    st, d = srv.json_req("POST", "/api/v1/reminders",
                         {"request_id": "ok", "profile_id": "default", "text": "x",
                          "due_at_utc": "2030-01-01T00:00:00Z"})
    print("normal create:", st, "envelope ok:", d.get("code") == 0)

    # 外部持 EXCLUSIVE 锁
    ext = sqlite3.connect(str(srv.db), isolation_level=None)
    ext.execute("BEGIN EXCLUSIVE")
    ext.execute("INSERT INTO settings(key,value,updated_at) VALUES('qa3lock','1','2020')")
    print("外部已持 BEGIN EXCLUSIVE 锁")

    t = time.time()
    st, d = srv.json_req("POST", "/api/v1/reminders",
                         {"request_id": "locked", "profile_id": "default", "text": "y",
                          "due_at_utc": "2030-01-01T00:00:00Z"}, timeout=20)
    dt = time.time() - t
    print(f"锁中写入 -> status={st} 耗时{dt:.1f}s body={str(d)[:120]}")

    # 读请求在锁中的表现
    st2, d2 = srv.json_req("GET", "/api/v1/memories?profile_id=default", timeout=20)
    print(f"锁中读取 -> status={st2} body={str(d2)[:100]}")

    ext.execute("ROLLBACK")
    ext.close()
    print("锁已释放")
    time.sleep(0.3)
    st3, d3 = srv.json_req("POST", "/api/v1/reminders",
                           {"request_id": "after", "profile_id": "default", "text": "z",
                            "due_at_utc": "2030-01-01T00:00:00Z"})
    print("释放后写入 -> status=", st3, "code=", d3.get("code") if isinstance(d3, dict) else d3)

    # 服务仍存活？
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("after: state", snap["data"]["state"], "alive", snap["data"]["worker_alive"],
          "faults", snap["data"]["worker_faults"])

    print("\n--- 服务端异常日志 ---")
    log = srv.log_text()
    for ln in log.splitlines():
        if "Error" in ln or "Traceback" in ln or "sqlite" in ln.lower() or "locked" in ln.lower():
            print("  ", ln[:160])
finally:
    srv.stop()
