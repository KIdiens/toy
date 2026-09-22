"""Self-check for host/web/static (run-time verification, not shipped)."""
import os
import re

ROOT = r"F:/AI智能底座玩具/toy"
STATIC = os.path.join(ROOT, "host/web/static")
TOKENS = os.path.join(ROOT, "host/web/design/design-tokens.css")
FIXTURE = os.path.join(ROOT, "tests/fixtures/lucide-1.47.0-icon-ids.txt")

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U0000FE0F\U00002190-\U000021FF\U00002700-\U000027BF"
    "\U0001F000-\U0001F0FF\U00002194-\U00002199\U000023E9-\U000023FA]"
)
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}|rgb\(|rgba\(|hsl\(|hsla\(")
GRAD_PINK = re.compile(r"gradient[^;]*?(purple|pink|#f0f|#e879f9|#c026d3|#a855f7)", re.I)


def read_text(path):
    """以 UTF-8 读取文本；用上下文管理器避免句柄泄漏（SIM115）。"""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


files = []
for root, _d, fs in os.walk(STATIC):
    for f in fs:
        if f.endswith((".html", ".js", ".css")):
            files.append(os.path.join(root, f))
files.sort()

print("=== 1) 文件与行数（硬约束：源文件 ≤300 行） ===")
over = []
for p in files:
    with open(p, encoding="utf-8") as fh:
        n = sum(1 for _ in fh)
    rel = os.path.relpath(p, STATIC)
    if n > 300:
        over.append((rel, n))
    print(f"   {n:5d}  {rel}")
print("   OVER-300:", over if over else "无")

print("\n=== 2) Emoji 扫描（范围 1F300-1FAFF/2600-27BF/2190-21FF/1F1E6-1F1FF/FE0F） ===")
emoji_hits = []
for p in files:
    txt = read_text(p)
    for i, line in enumerate(txt.splitlines(), 1):
        m = EMOJI.search(line)
        if m:
            emoji_hits.append((os.path.relpath(p, STATIC), i, repr(m.group(0))))
print("   命中数:", len(emoji_hits))
for h in emoji_hits:
    print("   HIT", h)

print("\n=== 3) 字面色值扫描（例外：#fff/#000 与 design-tokens.css） ===")
color_hits = []
for p in files:
    txt = read_text(p)
    for i, line in enumerate(txt.splitlines(), 1):
        for m in COLOR.finditer(line):
            v = m.group(0)
            if v.lower() in ("#fff", "#000", "#ffffff", "#000000"):
                continue
            color_hits.append((os.path.relpath(p, STATIC), i, v, line.strip()[:90]))
print("   命中数:", len(color_hits))
for h in color_hits[:60]:
    print("   HIT", h)
print("   tokens 文件是否唯一含色值落点:",
      os.path.relpath(TOKENS, ROOT), "存在=", os.path.isfile(TOKENS))

print("\n=== 4) 紫粉渐变扫描 ===")
grad_hits = []
for p in files:
    txt = read_text(p)
    if GRAD_PINK.search(txt):
        grad_hits.append(os.path.relpath(p, STATIC))
print("   命中数:", len(grad_hits), grad_hits)

print("\n=== 5) 图标 ID 核验（每个用到的 ic()/iconMarkup() 必须在 fixture 内） ===")
with open(FIXTURE, encoding="utf-8") as fh:
    fixture = {line.strip() for line in fh if line.strip()}
used = set()
for p in files:
    if not p.endswith(".js"):
        continue
    txt = read_text(p)
    used |= set(re.findall(r"""\bic\(\s*['"]([a-z0-9-]+)['"]""", txt))
    used |= set(re.findall(r"""iconMarkup\(\s*['"]([a-z0-9-]+)['"]""", txt))
# 组件模板中静态出现的 data-lucide 与 'x'/'moon' 三元写法
for p in files:
    txt = read_text(p)
    used |= set(re.findall(r"""data-lucide=["']([a-z0-9-]+)["']""", txt))
missing = sorted(u for u in used if u not in fixture)
print("   使用图标数:", len(used))
print("   不在 fixture 中的:", missing if missing else "无")
# 生成模块里实际打包的图标
gen = read_text(os.path.join(STATIC, "vendor/lucide-icons.js"))
packed = set(re.findall(r'^\s*"([a-z0-9-]+)":', gen, re.M))
print("   离线包内图标数:", len(packed), "| 用但未打包:", sorted(used - packed))
print("   打包但不在 fixture:", sorted(packed - fixture))

print("\n=== 6) 结构自检：页面/契约端点覆盖 ===")
for name in ["index.html", "editor.html", "memories.html", "reminders.html", "settings.html",
             "api.js", "app.js", "timeline.js", "styles.css", "styles-editor.css"]:
    print(f"   {name}: {'OK' if os.path.isfile(os.path.join(STATIC, name)) else 'MISSING'}")
api = read_text(os.path.join(STATIC, "api.js"))
for ep in ["/status", "/roles", "/roles/active", "/actions/validate", "/actions/play",
           "/actions/stop", "/content/import", "/memories", "/reminders", "/settings",
           "/features", "/events"]:
    print(f"   api 覆盖 {ep}: {'OK' if ep in api else 'MISSING'}")
