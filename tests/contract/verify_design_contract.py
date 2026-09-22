#!/usr/bin/env python3
"""设计契约守卫（CI 可跑）  —  作者：颜好看（UI/UX）

覆盖四类断言，缺一类不许报 PASS：
  A. 色值一致性：design-tokens.json 与 design-tokens.css 两个主题块逐项相等
  B. 字体：json font.family 与 css 三个变量相等
  C. P0 emoji：三文件按 P0 正则扫描为 0
  D. 接口契约：DESIGN.md 引用的每条 /api/ 路径必须带 v1 前缀，
     且双向对齐 docs/api/openapi.yaml（设计稿 ⊆ 契约 ∧ 契约写操作端点 ⊆ 设计稿）

用法：  python verify_design_contract.py     退出码：0 = PASS，1 = FAIL
位置无关：从脚本位置向上查找含 docs/api/openapi.yaml 的仓库根，
再由 root 定位设计产出目录（root/host/web/design）。放在 tests/contract/ 或任意 toy/ 子目录均可。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_up(relpath, start=HERE):
    """从 start 向上查找首个存在 relpath 的祖先目录，返回绝对路径或 None。"""
    p = start
    while True:
        cand = os.path.join(p, relpath)
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def find_design_dir(root):
    """定位设计产出目录：优先约定路径 root/host/web/design，否则在 root 下搜索。"""
    cand = os.path.join(root, "host", "web", "design")
    if os.path.exists(os.path.join(cand, "design-tokens.json")):
        return cand
    for dirpath, _dirnames, filenames in os.walk(root):
        if "design-tokens.json" in filenames and "DESIGN.md" in filenames:
            return dirpath
    return None


def css_block(css, selector):
    i = css.index(selector)
    j = css.index("{", i)
    k = css.index("}", j)
    return css[j:k]


def collect(block):
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)}


def parse_contract_paths(yaml_text):
    """极简 paths 解析：返回 {路径: {方法,...}}，不依赖 yaml 库。"""
    paths, current, in_paths = {}, None, False
    for line in yaml_text.splitlines():
        if re.match(r"^paths:\s*$", line):
            in_paths = True
            continue
        if in_paths and re.match(r"^[A-Za-z]", line):  # 顶层键结束 paths 段
            in_paths = False
        if not in_paths:
            continue
        m = re.match(r"^  (/[^:]*):\s*$", line)
        if m:
            current = m.group(1)
            paths[current] = set()
            continue
        m = re.match(r"^    (get|post|put|patch|delete|head|options):\s*$", line)
        if m and current:
            paths[current].add(m.group(1).upper())
    return paths


EMOJI_RE = re.compile("[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]")


def api_refs(text):
    """抽取真正的端点引用；左边界排除文件名（如 docs/api/openapi.yaml）。"""
    refs = set(re.findall(r"(?<![\w/.])/api/[A-Za-z0-9_\-/{}]*", text))
    return {r.rstrip(".,;)`") for r in refs}


def d1_violations(refs):
    return sorted(r for r in refs if not (r == "/api/v1" or r.startswith("/api/v1/")))


def selftest():
    """证明各断言确实能捕获对应违规（防"没覆盖也报绿"）。"""
    checks = []
    checks.append((
        "D1 捕获缺 v1 前缀",
        d1_violations(api_refs("调 `GET /api/status` 与 `GET /api/v1/status`"))
        == ["/api/status"],
    ))
    checks.append((
        "D1 不误伤文件名",
        d1_violations(api_refs("见 docs/api/openapi.yaml 与 `GET /api/v1/roles`")) == [],
    ))
    full = {"/api/v1/status"}
    miss = [r for r in api_refs("`GET /api/v1/nope`") if r != "/api/v1" and r not in full]
    checks.append(("D2 捕获契约外路径", miss == ["/api/v1/nope"]))
    checks.append(("C 捕获 emoji", bool(EMOJI_RE.search("hi \U0001F680"))))
    checks.append(("C 放行无 emoji", not EMOJI_RE.search("plain text")))
    ok = all(c[1] for c in checks)
    for name, passed in checks:
        print(f"  [{'ok' if passed else 'XX'}] {name}")
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    errors = []

    contract_file = find_up(os.path.join("docs", "api", "openapi.yaml"))
    if not contract_file:
        print("RESULT: FAIL\n  X 未找到契约文件 docs/api/openapi.yaml（应从 toy/ 树内运行）")
        return 1
    root = os.path.dirname(os.path.dirname(os.path.dirname(contract_file)))

    design_dir = find_design_dir(root)
    if not design_dir:
        print("RESULT: FAIL\n  X 未找到设计产出目录（design-tokens.json + DESIGN.md）")
        return 1
    with open(os.path.join(design_dir, "design-tokens.json"), encoding="utf-8") as fh:
        tokens = json.load(fh)
    with open(os.path.join(design_dir, "design-tokens.css"), encoding="utf-8") as fh:
        css = fh.read()
    with open(os.path.join(design_dir, "DESIGN.md"), encoding="utf-8") as fh:
        design = fh.read()

    # ---- A. 色值一致性 ----
    light = collect(css_block(css, ":root {"))
    dark = collect(css_block(css, '[data-theme="dark"] {'))

    def cmp(label, jv, cv):
        if str(jv).strip() != str(cv).strip():
            errors.append(f"[A] {label}: json={jv!r} css={cv!r}")

    for k, v in tokens["color"]["light"].items():
        cmp(f"color.light.{k}", v["value"], light.get(k))
    for k, v in tokens["color"]["dark"].items():
        cmp(f"color.dark.{k}", v["value"], dark.get(k))
    for k, v in tokens["color"]["domain"]["light"].items():
        cmp(f"color.domain.light.{k}", v["value"], light.get(k))
    for k, v in tokens["color"]["domain"]["dark"].items():
        cmp(f"color.domain.dark.{k}", v["value"], dark.get(k))

    # ---- B. 字体 ----
    for name in ("display", "body", "mono"):
        cmp(
            f"font.family.{name}",
            tokens["font"]["family"][name]["value"],
            light.get(f"font-{name}"),
        )
    cmp("shadow.ring", tokens["shadow"]["ring"]["value"], light.get("elev-ring"))

    # ---- C. P0 emoji ----
    for fn in ("DESIGN.md", "design-tokens.css", "design-tokens.json"):
        with open(os.path.join(design_dir, fn), encoding="utf-8") as fh:
            txt = fh.read()
        for m in EMOJI_RE.finditer(txt):
            errors.append(f"[C] emoji in {fn}: {m.group()!r}")

    # ---- D. 接口契约 ----
    refs = api_refs(design)

    # D1: 每条引用必须带 v1 前缀
    for r in d1_violations(refs):
        errors.append(f"[D1] 设计稿引用缺少 v1 前缀: {r}")

    with open(contract_file, encoding="utf-8") as fh:
        contract = parse_contract_paths(fh.read())
    contract_full = {"/api/v1" + p for p in contract}

    # D2: 设计稿引用的路径必须存在于契约（忽略 base /api/v1）
    for r in sorted(refs):
        if r == "/api/v1":
            continue
        if r not in contract_full:
            errors.append(f"[D2] 设计稿引用了契约中不存在的路径: {r}")

    # D3: 契约中的写操作端点必须在设计稿有归属
    write_full = {"/api/v1" + p for p, ms in contract.items()
                  if ms & {"POST", "PUT", "PATCH", "DELETE"}}
    for r in sorted(write_full):
        if r not in refs:
            errors.append(f"[D3] 契约写操作端点未在设计稿出现: {r}")

    # ---- 结果 ----
    n_v1 = len(re.findall(r"/api/v1/", design))
    ab_mismatches = [e for e in errors if e.startswith("[A]") or e.startswith("[B]")]
    print(f"[A/B] color+font mismatches : {len(ab_mismatches)}")
    print(f"[C]   emoji hits            : {len([e for e in errors if e.startswith('[C]')])}")
    print(f"[D]   /api/v1/ occurrences   : {n_v1}")
    print(f"[D]   design refs           : {len(refs)}  contract paths: {len(contract_full)}")
    print(f"total errors: {len(errors)}")
    for e in errors:
        print("  X", e)
    print("RESULT:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
