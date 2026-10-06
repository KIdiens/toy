"""Probe 11b: (A) CTRL_BREAK 是否真的走 finally（close/stop_workers）；
(B) 在并发写库中途硬杀 → integrity_check 与是否半写入。
"""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import PY, REPO, TMP, free_port  # noqa: E402

ENV = dict(os.environ)
ENV["NO_PROXY"] = "127.0.0.1,localhost"
ENV["PYTHONUNBUFFERED"] = "1"


def start(port, db, group=False, capture=False):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if group else 0
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    return subprocess.Popen([PY, "-m", "host.main", "--simulate",
                             "--port", str(port), "--db", str(db)],
                            cwd=str(REPO), env=ENV, stdout=out,
                            stderr=subprocess.STDOUT, text=capture,
                            creationflags=flags)


def wait_ready(port, t=20):
    import http.client
    dl = time.time() + t
    while time.time() < dl:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            c.request("GET", "/api/v1/status")
            if c.getresponse().status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


print("=== A) CTRL_BREAK 优雅关停：finally 是否执行 close() ===")
port = free_port(); db = TMP / "grace2.sqlite3"
if db.exists():
    db.unlink()
p = start(port, db, group=True, capture=True)
assert wait_ready(port)
p.send_signal(signal.CTRL_BREAK_EVENT)
try:
    out = p.communicate(timeout=12)[0]
except subprocess.TimeoutExpired:
    p.kill(); out = p.communicate()[0]
print("rc:", p.returncode)
for ln in (out or "").splitlines():
    if "收尾" in ln or "stopped" in ln.lower() or "shutdown" in ln.lower() or "关闭数据库" in ln:
        print("   LOG:", ln)
print("  finally 行出现?", "开始收尾" in (out or ""))

print("\n=== B) 并发写库中途硬杀 → integrity ===")
port2 = free_port(); db2 = TMP / "killwrites.sqlite3"
if db2.exists():
    db2.unlink()
p = start(port2, db2)
assert wait_ready(port2)
stop = threading.Event()
sent = [0]


def writer(i):
    import http.client, json
    n = 0
    while not stop.is_set():
        try:
            c = http.client.HTTPConnection("127.0.0.1", port2, timeout=3)
            c.request("POST", "/api/v1/reminders",
                      json.dumps({"request_id": f"w{i}-{n}", "profile_id": "default",
                                  "text": f"t{i}-{n}", "due_at_utc": "2030-01-01T00:00:00Z"}),
                      {"Content-Type": "application/json"})
            c.getresponse().read()
            n += 1; sent[0] += 1
        except Exception:
            return


ts = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(8)]
for t in ts:
    t.start()
time.sleep(2.5)  # 让写入持续进行
p.kill()  # 硬杀（写进行中）
stop.set()
for t in ts:
    t.join(timeout=3)
time.sleep(0.5)
print("kill 前写入请求数≈", sent[0])
con = sqlite3.connect(str(db2))
try:
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]
    rows = con.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
    # 检查是否有非法行（half-write）：所有行字段非空
    bad = con.execute("SELECT COUNT(*) FROM reminders WHERE text IS NULL OR due_at_utc IS NULL").fetchone()[0]
    print("integrity:", integ, "rows:", rows, "half-write rows:", bad)
finally:
    con.close()
# 再重启一次确认可用
p2 = start(port2, db2)
ok = wait_ready(port2)
print("重启可用?", ok)
p2.kill()
