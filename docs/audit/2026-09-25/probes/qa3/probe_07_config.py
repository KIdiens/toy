"""Probe 07: 配置损坏 / 端口占用 / 非法 MOTION_PORT —— 启动期健壮性。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import PY, REPO, AUDIT, TMP, free_port, port_in_use  # noqa: E402

bad = TMP / "as_dir.sqlite3"
bad.mkdir(parents=True, exist_ok=True)  # 让 DB_PATH 指向一个目录

long_path = str(TMP / ("x" * 300) / "db.sqlite3")


def run(args, env_extra=None, wait=8.0, port=None):
    env = dict(os.environ)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env.update(env_extra or {})
    p = subprocess.Popen([PY, "-m", "host.main"] + args, cwd=str(REPO), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = ""
    deadline = time.time() + wait
    alive_mid = None
    try:
        while time.time() < deadline:
            if p.poll() is not None:
                break
            time.sleep(0.3)
        alive_mid = p.poll() is None
    finally:
        if p.poll() is None:
            p.kill()
        try:
            out = p.communicate(timeout=5)[0]
        except Exception:
            out = ""
    return p.returncode, alive_mid, out


print("========== 1) DB_PATH 指向目录 ==========")
rc, alive, out = run(["--simulate", "--port", str(free_port()), "--db", str(bad)])
print("rc:", rc, "stayed_alive:", alive)
print("OUTPUT:", out.strip()[-900:])

print("\n========== 2) DB_PATH 超长路径(>300) ==========")
rc, alive, out = run(["--simulate", "--port", str(free_port()), "--db", long_path])
print("rc:", rc, "stayed_alive:", alive)
print("OUTPUT:", out.strip()[-900:])

print("\n========== 3) MOTION_PORT 非法值 + simulate（应被忽略仍能起） ==========")
rc, alive, out = run(["--simulate", "--port", str(free_port()), "--db", str(TMP / "c3.sqlite3")],
                     env_extra={"MOTION_PORT": "not-a-real-port://%%%"})
print("rc:", rc, "stayed_alive:", alive)
print("OUTPUT:", out.strip()[-500:])

print("\n========== 4) HOST_PORT 被占用 ==========")
hold = socket.socket()
hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
hold.bind(("127.0.0.1", 0))
used_port = hold.getsockname()[1]
hold.listen(1)
rc, alive, out = run(["--simulate", "--port", str(used_port), "--db", str(TMP / "c4.sqlite3")], wait=8)
print("occupied port:", used_port, "rc:", rc, "stayed_alive:", alive)
print("OUTPUT:", out.strip()[-900:])
hold.close()

print("\n========== 5) 非 simulate + 非法 MOTION_PORT（启动应成功，播放时降级为故障） ==========")
port5 = free_port()
rc, alive, out = run(["--port", str(port5), "--db", str(TMP / "c5.sqlite3")],
                     env_extra={"MOTION_PORT": "COM_NOT_EXIST_999"}, wait=6)
print("rc:", rc, "stayed_alive:", alive)
print("OUTPUT:", out.strip()[-700:])
if alive:
    # 该进程已被 run() 杀掉；重起一个做播放测试
    import http.client, json
    env = dict(os.environ); env["NO_PROXY"] = "127.0.0.1,localhost"
    env["MOTION_PORT"] = "COM_NOT_EXIST_999"
    p = subprocess.Popen([PY, "-m", "host.main", "--port", str(port5), "--db", str(TMP / "c5b.sqlite3")],
                         cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    time.sleep(4)
    try:
        c = http.client.HTTPConnection("127.0.0.1", port5, timeout=5)
        c.request("GET", "/api/v1/status"); print("status:", c.getresponse().status)
        time.sleep(0.2)
        c = http.client.HTTPConnection("127.0.0.1", port5, timeout=8)
        body = json.dumps({"request_id": "x", "role_id": 1, "action_id": "nod"}).encode()
        c.request("POST", "/api/v1/actions/play", body, {"Content-Type": "application/json"})
        r = c.getresponse(); print("play:", r.status, r.read()[:200])
        time.sleep(1.5)
        c = http.client.HTTPConnection("127.0.0.1", port5, timeout=5)
        c.request("GET", "/api/v1/status"); s = json.loads(c.getresponse().read())
        print("after play:", s["data"]["state"], "fault:", s["data"]["fault"],
              "alive:", s["data"]["worker_alive"], "faults:", s["data"]["worker_faults"])
    finally:
        p.kill()
