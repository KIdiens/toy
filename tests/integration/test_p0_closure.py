"""P0 整改验收：走**真实进程 + 生产装配**的端到端（不在测试内自建 worker）。

为什么必须是真进程：本轮两个 P0 的共同漏网原因，是测试自建了生产里并不存在的
``_RoutingQueue`` / net worker。这里只起 ``python -m host.main``，用真 HTTP 观察。

覆盖：
- P0-1 硬门：两次 ``POST /dev/button`` → 进入 SPEAKING → 回 IDLE
- 提醒调度真的被驱动（``fire_due`` 有真实调用者 → status 变 fired）
- P0-2 硬门：注入不存在的串口后，worker 线程仍存活、故障可见、后续请求不永久失效
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.support import TOY_ROOT

STARTUP_TIMEOUT_S = 60.0
REQUEST_TIMEOUT_S = 20.0
DIALOG_TIMEOUT_S = 30.0
OBSERVE_TIMEOUT_S = 15.0
POLL_S = 0.05
LOG_TAIL_CHARS = 4000
BAD_SERIAL_PORT = "COM_NOPE_9999"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _tail(path: Path) -> str:
    if not path.is_file():
        return "(无服务日志)"
    return path.read_text(encoding="utf-8", errors="replace")[-LOG_TAIL_CHARS:]


def _wait_until_ready(base: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError("主机进程提前退出：\n" + _tail(log_path))
        try:
            response = httpx.get(base + "/api/v1/status", timeout=2.0, trust_env=False)
            if response.status_code == 200:
                return
            last = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(0.25)
    raise AssertionError(f"服务 {STARTUP_TIMEOUT_S}s 内未就绪（{last}）：\n" + _tail(log_path))


class _Server:
    """起一个真实主机进程（生产装配），模块结束收回。"""

    def __init__(self, workdir: Path, *, simulate: bool, env_extra: dict[str, str]) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = workdir / "server.log"
        env = dict(os.environ)
        env["DB_PATH"] = str(workdir / "toy.sqlite3")
        env["MOTION_BOOT_WAIT_S"] = "0.05"
        env.pop("DASHSCOPE_API_KEY", None)
        env.pop("AI_PROVIDER", None)
        env.update(env_extra)
        args = [sys.executable, "-m", "host.main", "--host", "127.0.0.1", "--port", str(self.port)]
        if simulate:
            args.append("--simulate")
        self._log_file = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            args, cwd=str(TOY_ROOT), env=env,
            stdout=self._log_file, stderr=subprocess.STDOUT, text=True,
        )

    def __enter__(self) -> _Server:
        _wait_until_ready(self.base, self.proc, self.log_path)
        return self

    def __exit__(self, *_exc) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=20)
        self._log_file.close()


@pytest.fixture(scope="module")
def dialog_server(tmp_path_factory):
    """无硬件档位（--simulate）：按键对话闭环。"""
    workdir = tmp_path_factory.mktemp("p0-dialog")
    with _Server(workdir, simulate=True, env_extra={}) as server:
        yield server


@pytest.fixture(scope="module")
def serial_server(tmp_path_factory):
    """真机档位但串口不存在：验证串口异常不再打穿 worker。"""
    workdir = tmp_path_factory.mktemp("p0-serial")
    with _Server(workdir, simulate=False, env_extra={"MOTION_PORT": BAD_SERIAL_PORT}) as server:
        yield server


def _client(server: _Server) -> httpx.Client:
    return httpx.Client(base_url=server.base, timeout=REQUEST_TIMEOUT_S, trust_env=False)


def _status(client: httpx.Client) -> dict:
    return client.get("/api/v1/status").json()["data"]


# ------------------------------------------------------------------ P0-1 硬门
def test_dialog_button_loop_reaches_speaking_then_idle(dialog_server: _Server) -> None:
    """两次按键 → SPEAKING → 回 IDLE。全程经真 HTTP，无测试内 worker。"""
    with _client(dialog_server) as client:
        assert _status(client)["worker_alive"] is True, "worker 未启动"

        first = client.post("/api/v1/dev/button", json={"request_id": "d-1", "n": 1})
        assert first.status_code == 200, first.text
        assert first.json()["data"]["state"] == "RECORDING"

        second = client.post("/api/v1/dev/button", json={"request_id": "d-2", "n": 1})
        assert second.status_code == 200, second.text
        assert second.json()["data"]["state"] == "THINKING"

        seen: list[str] = []
        deadline = time.monotonic() + DIALOG_TIMEOUT_S
        while time.monotonic() < deadline:
            state = _status(client)["state"]
            if not seen or seen[-1] != state:
                seen.append(state)
            if "SPEAKING" in seen and state == "IDLE":
                break
            time.sleep(POLL_S)

    assert "SPEAKING" in seen, f"未进入 SPEAKING（对话闭环断点未修好）：{seen}"
    assert seen[-1] == "IDLE", f"未在 {DIALOG_TIMEOUT_S}s 内回 IDLE：{seen}"


def test_reminder_is_actually_fired_by_tick(dialog_server: _Server) -> None:
    """提醒 tick 真的驱动了 fire_due：到期提醒会被领取并置为 fired。"""
    with _client(dialog_server) as client:
        created = client.post("/api/v1/reminders", json={
            "request_id": "r-1", "profile_id": "default", "text": "喝水",
            "due_at_utc": "2020-01-01T00:00:00Z", "timezone": "Asia/Shanghai",
        })
        assert created.status_code == 201, created.text

        status_seen = ""
        deadline = time.monotonic() + OBSERVE_TIMEOUT_S
        while time.monotonic() < deadline:
            rows = client.get("/api/v1/reminders", params={"profile_id": "default"}).json()["data"]
            if rows:
                status_seen = rows[-1]["status"]
                if status_seen == "fired":
                    break
            time.sleep(POLL_S)

    assert status_seen == "fired", f"提醒未被 tick 触发（当前 status={status_seen}）"


# ------------------------------------------------------------------ P0-2 硬门
def test_worker_survives_serial_open_failure(serial_server: _Server) -> None:
    """串口不存在时：故障可见、worker 仍存活、后续请求不永久失效。"""
    with _client(serial_server) as client:
        assert _status(client)["worker_alive"] is True

        played = client.post("/api/v1/actions/play", json={
            "request_id": "s-1", "role_id": 1, "action_id": "wave"})
        assert played.status_code == 200, played.text
        assert played.json()["data"]["scheduled"] is True

        snapshot: dict = {}
        deadline = time.monotonic() + OBSERVE_TIMEOUT_S
        while time.monotonic() < deadline:
            snapshot = _status(client)
            if snapshot.get("fault"):
                break
            time.sleep(POLL_S)

        assert snapshot.get("fault") is not None, "串口失败后故障未暴露到 /status"
        assert snapshot["worker_alive"] is True, "串口异常打穿了 worker 线程（P0-2 未修复）"

        # 后续请求不应永久失效：stop 不受故障锁定影响，且 worker 仍在消费队列
        stop = client.post("/api/v1/actions/stop", json={"request_id": "s-2"})
        assert stop.status_code == 200, stop.text
        assert _status(client)["worker_alive"] is True

    # 可诊断性：串口失败必须被记进日志（而不是静默打穿线程）
    assert "motion fault" in _tail(serial_server.log_path), "串口故障未记入日志"
