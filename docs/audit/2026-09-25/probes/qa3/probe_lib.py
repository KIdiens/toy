"""QA3 复查探针共享库（只读审计，仓库外运行）。

- 用真实子进程起服务（python -m host.main --simulate）
- 用 http.client 发原始请求（不经 requests/urllib，天然绕开 http_proxy 劫持）
- 提供 psutil 采样（RSS/句柄/线程）与端口占用检测
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PY = r"C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
REPO = Path(r"F:/AI智能底座玩具/toy")
AUDIT = Path(r"F:/AI智能底座玩具/_audit_qa3")
TMP = AUDIT / "tmp"
TMP.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO))  # 允许进程内 import host（供 monkeypatch 类探针）


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


class Server:
    """真实子进程服务封装。"""

    def __init__(self, *, port: int | None = None, db: Path | None = None,
                 simulate: bool = True, env_extra: dict[str, str] | None = None,
                 args: list[str] | None = None):
        self.port = port or free_port()
        self.db = db or (TMP / f"db_{self.port}.sqlite3")
        self.simulate = simulate
        self.env_extra = env_extra or {}
        self.args = args
        self.proc: subprocess.Popen | None = None
        self.log_path = TMP / f"server_{self.port}.log"

    def start(self, ready_timeout: float = 25.0) -> "Server":
        env = dict(os.environ)
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        env["PYTHONUNBUFFERED"] = "1"
        env.update(self.env_extra)
        cmd = [PY, "-m", "host.main"]
        if self.args is not None:
            cmd += self.args
        else:
            if self.simulate:
                cmd.append("--simulate")
            cmd += ["--port", str(self.port), "--db", str(self.db)]
        self._logf = open(self.log_path, "wb")
        self.proc = subprocess.Popen(cmd, cwd=str(REPO), env=env,
                                     stdout=self._logf, stderr=subprocess.STDOUT)
        self._wait_ready(ready_timeout)
        return self

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"server 提前退出 code={self.proc.returncode}\n"
                                   f"{self.log_text()[-2000:]}")
            try:
                code, _ = self.get("/api/v1/status")
                if code == 200:
                    return
            except OSError:
                pass
            time.sleep(0.15)
        raise RuntimeError(f"server 未就绪（{timeout}s）\n{self.log_text()[-2000:]}")

    # ---------- HTTP ----------
    def _conn(self, timeout: float = 10.0) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float = 10.0):
        conn = self._conn(timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, raw
        finally:
            conn.close()

    def get(self, path: str, timeout: float = 10.0):
        return self.request("GET", path, timeout=timeout)

    def json_req(self, method: str, path: str, payload=None, timeout: float = 10.0):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        status, raw = self.request(method, path, body, headers, timeout=timeout)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = raw.decode("utf-8", "replace")
        return status, data

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def stop(self, graceful: bool = False, timeout: float = 6.0) -> int | None:
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            if graceful:
                self.proc.terminate()  # Windows 上是硬杀；CTRL_BREAK 另测
            else:
                self.proc.kill()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=timeout)
        code = self.proc.returncode
        try:
            self._logf.close()
        except Exception:
            pass
        return code


def sample_proc(pid: int) -> dict:
    import psutil
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "rss_mb": round(p.memory_info().rss / 1024 / 1024, 2),
                "handles": p.num_handles() if hasattr(p, "num_handles") else None,
                "threads": p.num_threads(),
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def child_pids(pid: int) -> list[int]:
    import psutil
    try:
        p = psutil.Process(pid)
        return [c.pid for c in p.children(recursive=True)]
    except Exception:
        return []


def kill_tree(pid: int) -> None:
    import psutil
    try:
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            try:
                c.kill()
            except Exception:
                pass
        p.kill()
    except Exception:
        pass


def line(tag: str, msg: str = "") -> None:
    print(f"[{tag}] {msg}")
