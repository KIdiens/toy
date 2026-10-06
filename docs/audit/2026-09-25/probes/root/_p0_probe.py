"""独立 P0-1 探针：真起主机进程，高频采样状态序列，验证按键对话闭环。

与本仓既有测试的区别：本探针**不复用** tests/ 里的任何断言，自带起停与判定，
用于交叉验证「两次按键 → RECORDING → THINKING → SPEAKING → IDLE」确实成立，
并额外检查 worker 存活、故障计数、日志无异常、以及第二轮可重复。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
PY = r"C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
POLL_S = 0.02
ROUND_TIMEOUT_S = 25.0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=5) as raw:  # noqa: S310  本机 http
        return json.loads(raw.read().decode("utf-8"))


def post(base: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method="POST",
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as raw:  # noqa: S310
            return json.loads(raw.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"_status": exc.code, "_body": exc.read().decode("utf-8", "replace")}


def sample_states(base: str, deadline_s: float, stop_at_idle: bool) -> list[str]:
    """高频采样状态；返回状态变化序列（相邻去重）。"""
    seen: list[str] = []
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            state = get(base, "/api/v1/status")["data"]["state"]
        except Exception:  # noqa: BLE001  采样期间的服务抖动不应中止探针
            time.sleep(POLL_S)
            continue
        if not seen or seen[-1] != state:
            seen.append(state)
        if stop_at_idle and len(seen) > 1 and state == "IDLE":
            break
        time.sleep(POLL_S)
    return seen


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    workdir = Path(os.environ.get("TEMP", ".")) / f"p0probe-{port}"
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "probe-server.log"

    env = dict(os.environ)
    env["DB_PATH"] = str(workdir / "toy.sqlite3")
    env["MOTION_BOOT_WAIT_S"] = "0.05"
    env.pop("DASHSCOPE_API_KEY", None)
    env.pop("AI_PROVIDER", None)
    # 清掉代理，避免本机请求被沙箱环境变量劫持成绝对形式
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)

    verdict: dict = {"steps": []}
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [PY, "-m", "host.main", "--simulate", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(TOY_ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    verdict["fatal"] = "服务提前退出"
                    break
                try:
                    if get(base, "/api/v1/status").get("code") == 0:
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
            else:
                verdict["fatal"] = "服务 60s 内未就绪"

            if "fatal" not in verdict:
                boot = get(base, "/api/v1/status")["data"]
                verdict["boot_state"] = boot["state"]
                verdict["worker_alive_boot"] = boot["worker_alive"]

                for round_no in (1, 2):
                    entry: dict = {"round": round_no}
                    r1 = post(base, "/api/v1/dev/button", {"request_id": f"p{round_no}-1", "n": 1})
                    entry["after_press1"] = r1.get("data", {}).get("state", r1)
                    r2 = post(base, "/api/v1/dev/button", {"request_id": f"p{round_no}-2", "n": 1})
                    entry["after_press2"] = r2.get("data", {}).get("state", r2)
                    sequence = sample_states(base, ROUND_TIMEOUT_S, stop_at_idle=True)
                    entry["sequence"] = sequence
                    entry["reached_speaking"] = "SPEAKING" in sequence
                    entry["returned_idle"] = bool(sequence) and sequence[-1] == "IDLE"
                    entry["reached_thinking"] = "THINKING" in sequence
                    verdict["steps"].append(entry)
                    time.sleep(0.3)

                final = get(base, "/api/v1/status")["data"]
                verdict["final"] = {
                    k: final[k] for k in
                    ("state", "worker_alive", "worker_faults", "session_generation", "fault")
                }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    verdict["log_errors"] = [
        line for line in log_text.splitlines()
        if " ERROR " in line or " CRITICAL " in line
    ][:20]
    verdict["log_warnings"] = [
        line for line in log_text.splitlines() if " WARNING " in line
    ][:20]
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
