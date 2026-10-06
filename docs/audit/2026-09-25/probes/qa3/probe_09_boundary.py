"""Probe 09: 边界输入 —— 超长/超大整数/负数/NaN/Infinity/空/类型错配/深嵌套/U+202E。

只打 HTTP 层，记录状态码与信封形状（是否为统一 {code,data,message}）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server  # noqa: E402

srv = Server().start()
HDR = {"Content-Type": "application/json"}


def raw(method, path, body_bytes, timeout=15):
    try:
        st, b = srv.request(method, path, body_bytes, HDR, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return ("EXC", False, f"{type(exc).__name__}: {exc}")
    try:
        import json
        d = json.loads(b.decode("utf-8"))
        envelope = isinstance(d, dict) and set(d.keys()) >= {"code", "data", "message"}
        return st, envelope, d
    except Exception:
        return st, False, b[:160].decode("utf-8", "replace")


def show(tag, r):
    st, env, d = r
    msg = d.get("message") if isinstance(d, dict) else str(d)[:120]
    print(f"{tag:52s} -> status={st} envelope={env} msg={str(msg)[:90]}")


print("== 提醒创建：due_at_utc 各类畸形 ==")
base = {"request_id": "bk", "profile_id": "default", "text": "喝水"}
import json
cases = [
    ("not-a-date", {"due_at_utc": "not-a-date"}),
    ("empty due", {"due_at_utc": ""}),
    ("number due", {"due_at_utc": 12345}),
    ("null due", {"due_at_utc": None}),
    ("array due", {"due_at_utc": ["2020-01-01"]}),
    ("huge year", {"due_at_utc": "999999999999-01-01T00:00:00Z"}),
    ("unicode due", {"due_at_utc": "2020-01-01T00:00:00Z\u202e"}),
    ("text 1M chars", {"due_at_utc": "2020-01-01T00:00:00Z", "text": "A" * 1_000_000}),
    ("text U+202E", {"due_at_utc": "2020-01-01T00:00:00Z", "text": "喝\u202e水"}),
    ("text int", {"due_at_utc": "2020-01-01T00:00:00Z", "text": 123}),
    ("idem 100 chars", {"due_at_utc": "2020-01-01T00:00:00Z",
                        "idempotency_key": "k" * 100}),
]
for tag, extra in cases:
    payload = dict(base); payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    show(f"POST /reminders [{tag}]", raw("POST", "/api/v1/reminders", body))

print("\n== 动作播放：role_id / action_id 边界 ==")
for tag, payload in [
    ("role 2**64", {"request_id": "p", "role_id": 2**64, "action_id": "nod"}),
    ("role -1", {"request_id": "p", "role_id": -1, "action_id": "nod"}),
    ("role 0", {"request_id": "p", "role_id": 0, "action_id": "nod"}),
    ("role NaN", {"request_id": "p", "role_id": float("nan"), "action_id": "nod"}),
    ("role Infinity", {"request_id": "p", "role_id": float("inf"), "action_id": "nod"}),
    ("role string", {"request_id": "p", "role_id": "1", "action_id": "nod"}),
    ("action_id 1M", {"request_id": "p", "role_id": 1, "action_id": "A" * 1_000_000}),
    ("action_id empty", {"request_id": "p", "role_id": 1, "action_id": ""}),
    ("request_id null", {"request_id": None, "role_id": 1, "action_id": "nod"}),
]:
    body = json.dumps(payload).encode("utf-8")
    show(f"POST /actions/play [{tag}]", raw("POST", "/api/v1/actions/play", body))

print("\n== 原始非法 JSON（NaN/Infinity 字面量）==")
show("reminders body=NaN", raw("POST", "/api/v1/reminders", b"NaN"))
show("reminders body=Infinity", raw("POST", "/api/v1/reminders", b"Infinity"))
show("reminders body={}", raw("POST", "/api/v1/reminders", b"{}"))
show("reminders body=[]", raw("POST", "/api/v1/reminders", b"[]"))
show("reminders body=null", raw("POST", "/api/v1/reminders", b"null"))

print("\n== 深嵌套 JSON（/actions/validate）==")
depth = 5000
nested = ("[" * depth) + ("]" * depth)
body = ('{"request_id":"v","action":{"x":' + nested + "}}").encode("utf-8")
show(f"validate depth={depth}", raw("POST", "/api/v1/actions/validate", body, timeout=20))

print("\n== GET 查询串边界（阈值探测，按序不崩）==")
for n in (1000, 8000, 100_000, 1_000_000):
    show(f"GET /memories?profile_id=<{n} 个 9>",
         raw("GET", "/api/v1/memories?profile_id=" + "9" * n, b"", timeout=20))
show("GET /memories?profile_id=-1", raw("GET", "/api/v1/memories?profile_id=-1", b""))
show("GET /memories?profile_id=NaN", raw("GET", "/api/v1/memories?profile_id=NaN", b""))
show("DELETE /reminders/abc", raw("DELETE", "/api/v1/reminders/abc", b""))
show("DELETE /reminders/999999999999999999999999",
     raw("DELETE", "/api/v1/reminders/999999999999999999999999", b""))
# 服务是否因超长 URL 的自重置而受影响
show("GET /status 复检", raw("GET", "/api/v1/status", b""))

print("\n== 服务是否仍存活 ==")
st, d = srv.json_req("GET", "/api/v1/status")
print("status:", st, d.get("data", {}).get("state"), "alive:",
      d.get("data", {}).get("worker_alive"), "faults:", d.get("data", {}).get("worker_faults"))

srv.stop()
print("SERVER_EXIT done")
