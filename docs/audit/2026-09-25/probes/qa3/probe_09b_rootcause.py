"""Probe 09b: 捕获 500 的服务端根因（未包装异常打穿信封）。

针对：GET /memories?profile_id=<大整数>、DELETE /reminders/<大整数>、
      POST /reminders due_at_utc 畸形。
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402

srv = Server().start()
HDR = {"Content-Type": "application/json"}


def hit(tag, method, path, body=None):
    b = None if body is None else json.dumps(body).encode()
    st, raw = srv.request(method, path, b, HDR if b else {}, timeout=10)
    try:
        d = json.loads(raw.decode())
        env = isinstance(d, dict) and set(d.keys()) >= {"code", "data", "message"}
        print(f"{tag:44s} status={st} envelope={env} body={raw[:110]}")
    except Exception:
        print(f"{tag:44s} status={st} body={raw[:110]}")


# 触发三种 500
hit("GET memories profile_id=9*40", "GET", "/api/v1/memories?profile_id=" + "9" * 40)
hit("DELETE reminders id=9*30", "DELETE", "/api/v1/reminders/" + "9" * 30)
hit("POST reminders bad due", "POST", "/api/v1/reminders",
    {"request_id": "b", "profile_id": "default", "text": "x", "due_at_utc": "not-a-date"})
hit("POST reminders huge-year", "POST", "/api/v1/reminders",
    {"request_id": "b", "profile_id": "default", "text": "x",
     "due_at_utc": "999999999999-01-01T00:00:00Z"})

# 边界：多少位整数会 422 vs 500
for n in (10, 18, 19, 20, 30, 100, 4000, 4300, 5000):
    hit(f"GET memories profile_id=9*{n}", "GET", "/api/v1/memories?profile_id=" + "9" * n)

print("\n--- 服务端日志中的未捕获异常 ---")
log = srv.log_text()
import re
blocks = re.split(r"\n(?=\d{4}-\d{2}-\d{2})", log)
for b in blocks:
    if "Traceback" in b or "Error" in b:
        print(b[:1400])
        print("................")
srv.stop()
print("SERVER_EXIT done")
