"""Probe 10: 坏角色包导入 —— 校验失败必须 400（统一信封）且**旧版本不受影响**。

只发「应当失败」的包；不发送合法包（避免改写仓库 content/roles）。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r"F:/AI智能底座玩具/_audit_qa3")
from probe_lib import Server, REPO, TMP  # noqa: E402

BOUNDARY = "----qa3boundary"


def multipart(fields: dict, file_name: str, file_bytes: bytes) -> tuple[bytes, dict]:
    parts = []
    for k, v in fields.items():
        parts.append(f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    head = ("".join(parts)).encode()
    file_head = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"{file_name}\"\r\nContent-Type: application/zip\r\n\r\n").encode()
    tail = f"\r\n--{BOUNDARY}--\r\n".encode()
    body = head + file_head + file_bytes + tail
    headers = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
               "Content-Length": str(len(body))}
    return body, headers


def make_zip(entries: list[tuple[str, bytes]], *, symlink: bool = False,
             ratio_bomb: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            zi = zipfile.ZipInfo(name)
            if symlink:
                zi.external_attr = (0o120777 << 16)
            z.writestr(zi, data)
    return buf.getvalue()


def repo_hashes() -> dict:
    out = {}
    for p in sorted((REPO / "content" / "roles").glob("*/role.json")):
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


GOOD_ROLE = json.loads((REPO / "content" / "roles" / "1" / "role.json").read_text(encoding="utf-8"))

cases = {
    "非zip内容": b"this is definitely not a zip file",
    "空文件": b"",
    "缺role.json": make_zip([("readme.txt", b"hello")]),
    "role.json非法JSON": make_zip([("role.json", b"{not valid json")]),
    "role.json schema非法": make_zip([("role.json", json.dumps({"role_id": 1}).encode())]),
    "路径穿越 ../": make_zip([("role.json", json.dumps(GOOD_ROLE).encode()),
                             ("../evil.txt", b"x")]),
    "绝对路径 /etc": make_zip([("role.json", json.dumps(GOOD_ROLE).encode()),
                              ("/etc/passwd", b"x")]),
    "符号链接": make_zip([("role.json", json.dumps(GOOD_ROLE).encode()),
                         ("link", b"/etc/passwd")], symlink=True),
    "可执行后缀 .py": make_zip([("role.json", json.dumps(GOOD_ROLE).encode()),
                               ("run.py", b"print(1)")]),
    "压缩炸弹(高压缩比)": make_zip([("role.json", json.dumps(GOOD_ROLE).encode()),
                                    ("bomb.bin", b"\x00" * (3 * 1024 * 1024))]),
    "超大上传(>20MB)": b"PK\x03\x04" + b"\x00" * (21 * 1024 * 1024),
}

before = repo_hashes()
print("repo role.json hashes BEFORE:", before)

srv = Server().start()
try:
    for tag, payload in cases.items():
        body, headers = multipart({"request_id": "imp"}, "rolepack.zip", payload)
        st, raw = srv.request("POST", "/api/v1/content/import", body, headers, timeout=60)
        try:
            d = json.loads(raw.decode("utf-8"))
            env = isinstance(d, dict) and set(d.keys()) >= {"code", "data", "message"}
            msg = d.get("message", "")[:80]
        except Exception:
            env = False
            msg = raw[:80]
        print(f"{tag:22s} -> status={st} envelope={env} msg={msg}")

    # 服务仍存活？角色仍可用？
    st, roles = srv.json_req("GET", "/api/v1/roles")
    print("roles after:", st, [(r['role_id'], r['config_version']) for r in roles.get('data', [])])
    st, snap = srv.json_req("GET", "/api/v1/status")
    print("state:", snap["data"]["state"], "alive:", snap["data"]["worker_alive"],
          "faults:", snap["data"]["worker_faults"])
finally:
    srv.stop()

after = repo_hashes()
print("repo role.json hashes AFTER :", after)
print("REPO 未变?", before == after)
# 检查是否留下 .bak / .incoming 残留
residue = [str(p) for p in (REPO / "content" / "roles").glob("**/*")
           if p.name.endswith((".bak", ".incoming")) or ".incoming" in p.name or ".bak" in p.name]
print("残留 .bak/.incoming:", residue)
