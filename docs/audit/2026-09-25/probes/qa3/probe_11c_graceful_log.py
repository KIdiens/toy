"""Probe 11c: 精确定位关停路径是否执行 main() 的 finally。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import PY, REPO, TMP, free_port  # noqa: E402

ENV = dict(os.environ)
ENV["NO_PROXY"] = "127.0.0.1,localhost"
ENV["PYTHONUNBUFFERED"] = "1"


def run_and_stop(sig_name):
    port = free_port()
    db = TMP / f"grace_{sig_name}.sqlite3"
    if db.exists():
        db.unlink()
    p = subprocess.Popen([PY, "-m", "host.main", "--simulate", "--port", str(port), "--db", str(db)],
                         cwd=str(REPO), env=ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    import http.client
    dl = time.time() + 20
    while time.time() < dl:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            c.request("GET", "/api/v1/status")
            if c.getresponse().status == 200:
                break
        except OSError:
            time.sleep(0.2)
    if sig_name == "BREAK":
        p.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        p.send_signal(signal.CTRL_C_EVENT)
    try:
        out = p.communicate(timeout=12)[0]
    except subprocess.TimeoutExpired:
        p.kill(); out = p.communicate()[0]
    print(f"===== 信号 {sig_name} rc={p.returncode} 是否执行 finally =====")
    print("finally(开始收尾) 出现?", "开始收尾" in out)
    print("--- 全量输出 ---")
    print(out)
    print("--- /全量输出 ---")


run_and_stop("C")
run_and_stop("BREAK")
