"""前端只读审计探针（仓库之外，不修改任何项目文件）。

目的：用真实 headless Chrome + 有数据的 mock 后端，验证：
  1) /memories 列表行是否真的显示记忆内容（核查 MemoryList 行的 .memory 赋值时机）
  2) /reminders 列表行是否真的显示提醒内容（核查 ReminderList 行的 .reminder 赋值时机）
  3) /editor 是否会请求 GET /api/v1/actions/files（核查该端点是否被前端接入）
运行： python probe_lists.py <outfile>
"""
import os
import sys
import json
import threading
import subprocess
import http.server
import socketserver

ROOT = r"F:/AI智能底座玩具/toy"
STATIC = os.path.join(ROOT, "host/web/static")
DESIGN = os.path.join(ROOT, "host/web/design")
OUTDIR = r"F:/AI智能底座玩具/_audit_frontend"
PROFILE = os.path.join(OUTDIR, "_chrome_profile")
PORT = 8791
CHROME = next((p for p in [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
] if os.path.isfile(p)), None)

PAGES = {"/": "index.html", "/editor": "editor.html", "/memories": "memories.html",
         "/reminders": "reminders.html", "/settings": "settings.html"}

SEEN_PATHS = []

MEMORY_VALUE = "我喜欢恐龙"
REMINDER_TEXT = "该刷牙啦"


def envelope(data):
    return json.dumps({"code": 0, "data": data, "message": ""}, ensure_ascii=False).encode("utf-8")


MOCKS = {
    "/api/v1/status": envelope({
        "state": "IDLE", "mcu_armed": False, "docked": True, "estop_healthy": True,
        "role_id": 1, "session_generation": 1, "network_online": False,
        "fault": None, "action": None, "worker_alive": True, "worker_faults": 0,
    }),
    "/api/v1/roles": envelope([
        {"role_id": 1, "name": "小熊", "config_version": "1.0.0", "tts_voice": "xiaoxiao", "language": "Auto"},
    ]),
    "/api/v1/features": envelope({"provider": "stub", "memory": True}),
    "/api/v1/settings": envelope({
        "volume_level": "mid", "language": "Auto", "memory_enabled": True,
        "content_rating_max": "kid", "wifi_ssid": "home-2.4g", "sim_mode": True,
    }),
    "/api/v1/memories": envelope([
        {"id": 1, "profile_id": "default", "kind": "preference", "value": MEMORY_VALUE,
         "confirmed": True, "created_at": "2026-09-21T10:00:00Z", "expires_at": None},
    ]),
    "/api/v1/reminders": envelope([
        {"id": "rmd-1", "profile_id": "default", "text": REMINDER_TEXT,
         "due_at_utc": "2026-09-22T12:00:00Z", "timezone": "Asia/Shanghai",
         "repeat_rule": "daily", "status": "pending", "last_fired_at": None,
         "idempotency_key": "k-1"},
    ]),
    "/api/v1/actions/files": envelope([
        {"name": "wave.json", "active": True}, {"name": "boot.json", "active": False},
    ]),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, fp, ctype):
        fp = os.path.normpath(fp)
        if not os.path.isfile(fp):
            self.send_error(404)
            return
        data = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _ctype(p):
        if p.endswith(".css"):
            return "text/css; charset=utf-8"
        if p.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if p.endswith(".html"):
            return "text/html; charset=utf-8"
        return "application/octet-stream"

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/"):
            SEEN_PATHS.append(path)
            if path in MOCKS:
                return self._json(200, MOCKS[path])
            return self._json(404, json.dumps({"code": 40400, "data": None, "message": "nope"}).encode())
        if path in PAGES:
            return self._file(os.path.join(STATIC, PAGES[path]), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._file(os.path.join(STATIC, path[len("/static/"):]), self._ctype(path))
        if path.startswith("/design/"):
            return self._file(os.path.join(DESIGN, path[len("/design/"):]), self._ctype(path))
        self.send_error(404)


def chrome_dom(url):
    os.makedirs(PROFILE, exist_ok=True)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
           "--no-proxy-server", "--no-first-run", "--hide-scrollbars",
           "--user-data-dir=" + PROFILE, "--virtual-time-budget=6000",
           "--dump-dom", url]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def main():
    out = open(sys.argv[1], "w", encoding="utf-8")
    def emit(*a):
        print(*a, file=out)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    emit("Chrome:", CHROME)

    # ---- /memories ----
    SEEN_PATHS.clear()
    dom = chrome_dom("http://127.0.0.1:%d/memories" % PORT).stdout
    emit("")
    emit("=== /memories ===")
    emit("  请求过的 API:", sorted(set(SEEN_PATHS)))
    emit("  DOM 含记忆正文 %r: %s" % (MEMORY_VALUE, MEMORY_VALUE in dom))
    emit("  DOM 含 '共 1 条记忆': %s" % ("共 1 条记忆" in dom))
    emit("  DOM 含 <memory-row>: %s" % ("memory-row" in dom))

    # ---- /reminders ----
    SEEN_PATHS.clear()
    dom = chrome_dom("http://127.0.0.1:%d/reminders" % PORT).stdout
    emit("")
    emit("=== /reminders ===")
    emit("  请求过的 API:", sorted(set(SEEN_PATHS)))
    emit("  DOM 含提醒正文 %r: %s" % (REMINDER_TEXT, REMINDER_TEXT in dom))
    emit("  DOM 含 '共 1 条提醒': %s" % ("共 1 条提醒" in dom))
    emit("  DOM 含 <reminder-row>: %s" % ("reminder-row" in dom))

    # ---- /editor ----
    SEEN_PATHS.clear()
    dom = chrome_dom("http://127.0.0.1:%d/editor" % PORT).stdout
    emit("")
    emit("=== /editor ===")
    emit("  请求过的 API:", sorted(set(SEEN_PATHS)))
    emit("  是否请求 /api/v1/actions/files: %s" % ("/api/v1/actions/files" in SEEN_PATHS))
    emit("  DOM 含 'wave.json': %s" % ("wave.json" in dom))
    emit("  文件列表空态可见: %s" % ("动作文件列表" in dom or "暂无动作文件" in dom))

    # ---- /settings (对照，已知应正常) ----
    SEEN_PATHS.clear()
    dom = chrome_dom("http://127.0.0.1:%d/settings" % PORT).stdout
    emit("")
    emit("=== /settings（对照）===")
    emit("  请求过的 API:", sorted(set(SEEN_PATHS)))
    emit("  DOM 含 SSID 值 home-2.4g: %s" % ("home-2.4g" in dom))

    srv.shutdown()
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
