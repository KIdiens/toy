"""无硬件全链路验收：真起 ``python -m host.main --simulate``，用真 HTTP 打通主流程。

覆盖：状态可读 → 播放（带 request_id + 预期 role_id）→ 第二次播放 409 → 模拟拔出
→ ``FAULT_LOCKED`` → 确认解除 → 回到 IDLE。全部经真实子进程与真实端口，非进程内桩。
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
FAULT_POLL_TIMEOUT_S = 25.0
POLL_S = 0.25
LOG_TAIL_CHARS = 4000


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
            # trust_env=False：本机验收不得被环境里的 HTTP(S)_PROXY 劫持成绝对形式请求
            response = httpx.get(base + "/api/v1/status", timeout=2.0, trust_env=False)
            if response.status_code == 200:
                return
            last = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(POLL_S)
    raise AssertionError(f"服务 {STARTUP_TIMEOUT_S}s 内未就绪（{last}）：\n" + _tail(log_path))


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory):
    """启动一个真实的 ``--simulate`` 主机服务（进程 + 端口），模块结束收回。"""
    workdir = tmp_path_factory.mktemp("no-hardware")
    port = _free_port()
    env = dict(os.environ)
    env["DB_PATH"] = str(workdir / "toy.sqlite3")
    env.pop("DASHSCOPE_API_KEY", None)
    env.pop("AI_PROVIDER", None)

    log_path = workdir / "server.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "host.main", "--simulate",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(TOY_ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_until_ready(base, proc, log_path)
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)


@pytest.fixture(scope="module")
def client(server: str):
    # trust_env=False：直连本机端口，忽略沙箱/CI 里预设的 HTTP(S)_PROXY（否则请求会以
    # 绝对形式发到目标而 404，见 server.log 的 "GET http%3A//…/api/v1/… 404"）。
    with httpx.Client(base_url=server, timeout=REQUEST_TIMEOUT_S, trust_env=False) as http:
        yield http


def _wait_for_fault(client: httpx.Client) -> dict:
    deadline = time.monotonic() + FAULT_POLL_TIMEOUT_S
    snapshot: dict = {}
    while time.monotonic() < deadline:
        snapshot = client.get("/api/v1/status").json()["data"]
        if snapshot["state"] == "FAULT_LOCKED" or snapshot.get("fault"):
            return snapshot
        time.sleep(POLL_S)
    raise AssertionError(f"拔出后未在 {FAULT_POLL_TIMEOUT_S}s 内进入故障锁定：{snapshot}")


def test_full_no_hardware_chain(client: httpx.Client) -> None:
    # 1) 服务就绪且状态可读；无 Key 时网络标记为离线、会话代数已就位
    status = client.get("/api/v1/status").json()
    assert status["code"] == 0
    data = status["data"]
    assert data["state"] == "IDLE"
    assert data["role_id"] == 1
    assert data["docked"] is True and data["estop_healthy"] is True
    assert data["session_generation"] >= 1
    assert "api_key" not in client.get("/api/v1/status").text

    # 2) 无 DASHSCOPE_API_KEY ⇒ 桩后端（AC-17）
    assert client.get("/api/v1/features").json()["data"]["provider"] == "stub"

    # 3) 动作文件列表可读（编排器数据源）
    files = client.get("/api/v1/actions/files").json()["data"]
    assert {item["name"] for item in files} >= {"wave", "nod"}

    # 4) 播放：必须带 request_id 与预期 role_id
    played = client.post("/api/v1/actions/play", json={
        "request_id": "chain-1", "role_id": 1, "action_id": "wave"})
    assert played.status_code == 200
    assert played.json()["data"]["scheduled"] is True

    # 5) 第二次播放默认拒绝（不悄悄叠加两条时间轴，AC-16）
    second = client.post("/api/v1/actions/play", json={
        "request_id": "chain-2", "role_id": 1, "action_id": "wave"})
    assert second.status_code == 409
    assert second.json()["code"] == 40900
    assert second.json()["message"]

    # 6) 角色不匹配同样拒绝（防网页缓存串角色）
    wrong_role = client.post("/api/v1/actions/play", json={
        "request_id": "chain-3", "role_id": 2, "action_id": "wave"})
    assert wrong_role.status_code == 409

    # 7) 模拟拔出 → 联锁跳闸 → FAULT_LOCKED（AC-03/AC-24）
    assert client.post("/api/v1/dev/undock").status_code == 200
    locked = _wait_for_fault(client)
    assert locked["state"] == "FAULT_LOCKED"
    assert locked["fault"]["code"] == "INTERLOCK"

    # 8) 故障锁定下拒绝播放（唯一收敛路径是人工确认）
    blocked = client.post("/api/v1/actions/play", json={
        "request_id": "chain-4", "role_id": 1, "action_id": "wave"})
    assert blocked.status_code == 409

    # 8b) 公仔仍拔出时确认解除必须被拒（否则固件会立刻再跳闸，假可用态）
    refused = client.post("/api/v1/dev/clear").json()["data"]
    assert refused["cleared"] is False
    assert refused["reason"], "拒绝解除必须给出原因，前端要显示给用户"
    assert client.get("/api/v1/status").json()["data"]["state"] == "FAULT_LOCKED"

    # 8c) 把公仔插回去（只恢复硬件前置条件，**不**自动解除锁定）
    assert client.post("/api/v1/dev/dock").status_code == 200
    assert client.get("/api/v1/status").json()["data"]["state"] == "FAULT_LOCKED"

    # 9) 人工确认解除 → 回到 IDLE
    cleared = client.post("/api/v1/dev/clear").json()["data"]
    assert cleared["cleared"] is True
    assert client.get("/api/v1/status").json()["data"]["state"] == "IDLE"


def test_pages_and_static_are_mounted(client: httpx.Client) -> None:
    """挂载契约：六个人工页面 + 静态资源都必须能取到（避免 404）。"""
    for page in ("/", "/editor", "/roles", "/memories", "/reminders", "/settings"):
        response = client.get(page)
        assert response.status_code == 200, page
        assert response.headers["content-type"].startswith("text/html"), page

    stylesheet = client.get("/static/styles.css")
    assert stylesheet.status_code == 200
    assert "css" in stylesheet.headers["content-type"]
