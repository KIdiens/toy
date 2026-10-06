"""把 /memories 与 /reminders 的渲染后 DOM 落盘，便于逐行核对。"""
import os
import sys
import json
import threading
import subprocess
import http.server
import socketserver
import importlib.util

spec = importlib.util.spec_from_file_location(
    "probe_lists", r"F:/AI智能底座玩具/_audit_frontend/probe_lists.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

OUT = r"F:/AI智能底座玩具/_audit_frontend"

srv = socketserver.ThreadingTCPServer(("127.0.0.1", P.PORT), P.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

for name in ("memories", "reminders", "editor"):
    dom = P.chrome_dom("http://127.0.0.1:%d/%s" % (P.PORT, name)).stdout
    open(os.path.join(OUT, "dom_%s.html" % name), "w", encoding="utf-8").write(dom)

srv.shutdown()
print("dumped")
