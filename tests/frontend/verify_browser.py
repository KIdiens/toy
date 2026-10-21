"""Frontend browser verification (dev-time only; not shipped).

1) 用 threading HTTP server 复刻后端**静态挂载契约**：
     /            -> static/index.html（设备状态）
     /editor /roles /memories /reminders /settings -> 对应 HTML
     /static/*    -> host/web/static
     /design/*    -> host/web/design
     /api/v1/*    -> 503 信封（模拟"后端未启动"，验证离线降级）
2) 用 headless Chrome 逐页 --dump-dom 与 --screenshot，断言离线态真的渲染出来。

运行： python tests/frontend/verify_browser.py

**三个必须保留的 Chrome 参数（漏掉会截出完全错误的渲染结果）**：
  * --no-proxy-server   ：本机若存在 HTTP 代理，Chrome 会走代理，访问 127.0.0.1
                          的本地服务被拦或回落，必须显式禁用代理才能命中本地 server。
  * --user-data-dir=... ：复用默认用户目录会带上用户态的代理 / 扩展 / 缓存，
                          必须用独立临时目录，保证每次都是可复现的干净环境。
  * /api/* 必须回 JSON 信封（而非 HTML 404）：SimpleHTTPRequestHandler 默认对未知
                          路径回 HTML 错误页，前端 fetch 解析会失败并走进错误的降级
                          分支；本脚本显式让 /api/* 回 {"code": ...} 信封，才能真实验证
                          "离线降级"而不是"解析崩溃"。这三条是踩过坑的关键知识。
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading

# 从本文件位置推导工程根（与 tests/support.py 的 TOY_ROOT 同一约定）。
# 不得写死 "F:/AI智能底座玩具/toy"：该绝对路径在 Linux CI / 其它机器上不存在。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "host/web/static")
DESIGN = os.path.join(ROOT, "host/web/design")
SHOTS = os.path.join(ROOT, "_verify_shots")
PORT = 8765
# 浏览器候选表按平台给：Linux 上没有 .exe 的 C:/ 路径，写死会在 Ubuntu 恒为 None。
# 本脚本是 dev-time 手工验证，不进 CI（CI 不装浏览器，见 ci.yml注释）。
_CANDIDATES = {
    "win32": [
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge",
    ],
}
CHROME = next(
    (p for p in _CANDIDATES.get(sys.platform, []) if os.path.isfile(p)),
    None,
)
if CHROME is None:  # 兜底到 PATH，避免"装了 Chrome 但因路径不同就找不到"
    CHROME = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("microsoft-edge")
    )

ROUTES = {"/": "index.html", "/editor": "editor.html", "/roles": "roles.html",
          "/memories": "memories.html", "/reminders": "reminders.html",
          "/settings": "settings.html"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def _serve(self, path):
        if path.startswith("/api/"):
            body = json.dumps({"code": 5030, "message": "本机服务未启动（离线）"}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ROUTES:
            return self._file(os.path.join(STATIC, ROUTES[path]), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._file(os.path.join(STATIC, path[len("/static/"):]), self._ctype(path))
        if path.startswith("/design/"):
            return self._file(os.path.join(DESIGN, path[len("/design/"):]), self._ctype(path))
        self.send_error(404)

    @staticmethod
    def _ctype(p):
        if p.endswith(".css"):
            return "text/css; charset=utf-8"
        if p.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if p.endswith(".html"):
            return "text/html; charset=utf-8"
        if p.endswith(".json"):
            return "application/json"
        return "application/octet-stream"

    def _file(self, fp, ctype):
        fp = os.path.normpath(fp)
        if not os.path.isfile(fp) or not fp.replace("\\", "/").startswith(ROOT.replace("\\", "/")):
            self.send_error(404)
            return
        with open(fp, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve(self.path.split("?")[0])


def chrome(args, url):
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
           "--no-proxy-server", "--no-first-run", "--hide-scrollbars",
           "--user-data-dir=" + os.path.join(SHOTS, "_profile"), "--virtual-time-budget=6000",
           "--enable-logging=stderr", "--log-level=0"] + args + [url]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


MARKERS = {
    # 每页都要求：静态结构 + 组件已渲染 + 页面控制器已跑（证明离线降级真实生效）
    "index.html": ["axis-row", "conn--offline", "未检测到设备", "目标位置", "no-data",
                   "连接前置条件", "skip-link"],
    "editor.html": ["editor__rail", "tl__inner", "class=\"kf\"", "lane__band", "离线预演",
                    "关键帧属性", "校验结果"],
    "memories.html": ["记忆管理", "不保存原始录音", "清空全部记忆", "role=\"list\"",
                      "还什么都没记住" if False else "记忆列表"],
    "roles.html": ["角色性格", "选择角色", "编译预览", "persona-editor",
                   "这段文字会作为角色设定发给模型", "保存人设", "恢复默认人设"],
    "reminders.html": ["提醒管理", "新建提醒", "提醒时间", "重复", "仅一次"],
    "settings.html": ["音量", "儿童模式", "内容审核", "危险区", "SSID"],
}
# 额外证明页面控制器已执行（offline 分支）：正则匹配
RUNTIME = {
    "index.html": (r'id="meta-fetch">\d', "页脚已写入刷新时间（status 控制器已运行）"),
    "editor.html": (r'class="kf"', "时间轴已渲染菱形关键帧（editor 控制器已运行）"),
    "memories.html": (r'共 0 条记忆|共 \d+ 条记忆', "记忆计数已写入"),
    "reminders.html": (r'共 0 条提醒|共 \d+ 条提醒', "提醒计数已写入"),
    "settings.html": (r'id="offline-hint"', "离线提示位存在"),
    # 离线分支：角色列表取不到时必须显式说明（不是空白、也不是永远转圈）
    "roles.html": (r'没有读到角色列表', "角色控制器已跑完并渲染离线态"),
}


def main():
    if CHROME is None:
        # 明确报错而非让 subprocess 抛 FileNotFoundError：本脚本是 dev-time 手工
        # 验证，CI 不装浏览器（见 ci.yml），因此不进 pytest 门禁。
        print(
            "未找到 Chrome/Chromium/Edge。本脚本需要浏览器做 --dump-dom 渲染验证，\n"
            "请安装后重试，或改用无需浏览器的 node 回归脚本：\n"
            "    node tests/frontend/verify_batch2_fixes.mjs\n"
            "    node tests/frontend/verify_logic_and_render.mjs",
            file=sys.stderr,
        )
        return 2
    os.makedirs(SHOTS, exist_ok=True)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("Chrome:", CHROME)
    print(f"server: http://127.0.0.1:{PORT}")
    ok = True
    for route, fname in ROUTES.items():
        url = f"http://127.0.0.1:{PORT}{route}"
        dom = chrome(["--dump-dom"], url)
        html = dom.stdout
        png = os.path.join(SHOTS, fname.replace(".html", ".png"))
        chrome(["--screenshot=" + png, "--window-size=1440,1200"], url)
        icons = len(re.findall(r'class="ic"', html))
        missing = [m for m in MARKERS[fname] if m not in html]
        rx, rdesc = RUNTIME[fname]
        runtime_ok = re.search(rx, html) is not None
        console_err = [
            line for line in (dom.stderr or "").splitlines()
            if "CONSOLE" in line and re.search(r"error|Uncaught|Failed|SEVERE", line, re.I)
        ]
        status = "PASS" if not missing and icons > 0 and runtime_ok and not console_err else "FAIL"
        if status == "FAIL":
            ok = False
        shot_size = os.path.getsize(png) if os.path.isfile(png) else 0
        print(
            f"[{status}] {fname:16s} route={route:11s} dom={len(html):7d}B  "
            f"lucide图标={icons:3d}  截图={shot_size}B  控制器={runtime_ok}  "
            f"缺标记={missing or '无'}"
        )
        if not runtime_ok:
            print(f"        runtime 断言失败：{rdesc} /{rx}/")
        if console_err:
            print("        console errors:", console_err[:3])
    srv.shutdown()
    print("\n浏览器端验证：", "全部通过" if ok else "存在失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
