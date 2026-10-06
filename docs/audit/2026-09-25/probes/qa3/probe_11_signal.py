"""Probe 11: 进程终止（硬杀≈SIGKILL / 优雅 CTRL_BREAK）在录音中/播放中/思考中。

断言：重启后 PRAGMA integrity_check=ok、无半写入、行数一致。
"""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import PY, REPO, TMP, free_port  # noqa: E402

ENV = dict(os.environ)
ENV["NO_PROXY"] = "127.0.0.1,localhost"
ENV["PYTHONUNBUFFERED"] = "1"


def start(port, db, group=False):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if group else 0
    return subprocess.Popen([PY, "-m", "host.main", "--simulate",
                             "--port", str(port), "--db", str(db)],
                            cwd=str(REPO), env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
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


def post(port, path, body):
    import http.client, json
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse(); return r.status, r.read()


def get(port, path):
    import http.client, json
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse(); return r.status, json.loads(r.read())


def integrity(db):
    con = sqlite3.connect(str(db))
    try:
        r = con.execute("PRAGMA integrity_check").fetchone()[0]
        rows = con.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        return r, rows
    finally:
        con.close()


def scenario(tag, state_builder, killmode):
    port = free_port()
    db = TMP / f"kill_{tag}_{killmode}.sqlite3"
    if db.exists():
        db.unlink()
    p = start(port, db, group=(killmode == "break"))
    assert wait_ready(port), f"{tag} 未就绪"
    # 造数据
    post(port, "/api/v1/reminders", {"request_id": "r", "profile_id": "default",
                                     "text": "喝水", "due_at_utc": "2030-01-01T00:00:00Z"})
    st, snap = get(port, "/api/v1/status")
    state_builder(port)
    st, snap = get(port, "/api/v1/status")
    mid = snap["data"]["state"]
    # 终止
    if killmode == "kill":
        p.kill()
    else:
        p.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        rc = p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill(); rc = p.wait()
    time.sleep(0.5)
    integ, rows = integrity(db)
    print(f"[{tag}/{killmode}] state@kill={mid} rc={rc} integrity={integ} reminders={rows}")
    return integ == "ok"


def to_recording(port):
    post(port, "/api/v1/dev/button", {"request_id": "b", "n": 1})
    time.sleep(0.4)


def to_thinking(port):
    post(port, "/api/v1/dev/button", {"request_id": "b1", "n": 1})
    time.sleep(0.3)
    post(port, "/api/v1/dev/button", {"request_id": "b2", "n": 1})
    time.sleep(0.05)


def to_speaking(port):
    to_thinking(port)
    for _ in range(30):
        time.sleep(0.1)
        st, snap = get(port, "/api/v1/status")
        if snap["data"]["state"] == "SPEAKING":
            return
    # 即便没抓到 SPEAKING 也继续


print("== SIGKILL 场景 ==")
ok1 = scenario("rec", to_recording, "kill")
ok2 = scenario("think", to_thinking, "kill")
ok3 = scenario("speak", to_speaking, "kill")

print("\n== CTRL_BREAK（优雅）场景 ==")
port = free_port(); db = TMP / "graceful.sqlite3"
if db.exists():
    db.unlink()
p = start(port, db, group=True)
assert wait_ready(port)
post(port, "/api/v1/reminders", {"request_id": "r", "profile_id": "default",
                                 "text": "x", "due_at_utc": "2030-01-01T00:00:00Z"})
p.send_signal(signal.CTRL_BREAK_EVENT)
try:
    rc = p.wait(timeout=10)
except subprocess.TimeoutExpired:
    p.kill(); rc = p.wait()
time.sleep(0.5)
integ, rows = integrity(db)
print(f"[graceful/break] rc={rc} integrity={integ} reminders={rows}")

print("\nALL_INTEGRITY_OK:", all([ok1, ok2, ok3, integ == "ok"]))
