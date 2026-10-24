"""TCP 模拟器没有收到输入时也必须运行固件主循环。"""
from __future__ import annotations

import socket
import time

import pytest
from sim.tcp_bridge import TcpBridge, make_controller


def receive_until(client, predicate, *, timeout=1.2):
    deadline = time.monotonic() + timeout
    pending = b""
    seen = []
    while time.monotonic() < deadline:
        client.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            data = client.recv(4096)
        except TimeoutError:
            break
        assert data, "TCP 模拟器提前断开"
        pending += data
        while b"\n" in pending:
            raw, pending = pending.split(b"\n", 1)
            line = raw.decode("ascii").strip()
            seen.append(line)
            if predicate(line):
                return line
    pytest.fail(f"空闲固件没有发出目标状态，实际收到：{seen}")


@pytest.mark.parametrize("watchdog", [False, True], ids=["stable-role", "idle-watchdog"])
def test_idle_tcp_connection_keeps_sampling_reporting_and_watchdog(watchdog):
    bridge = TcpBridge(port=0, controller_factory=make_controller).start()
    try:
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=1) as client:
            # 不发送 STATUS 来推动时钟；稳定角色必须由固件自己的循环采样出来。
            receive_until(client, lambda line: line == "STATE 0 1 1 1")
            if watchdog:
                client.sendall(b"ARM 1\n")
                receive_until(client, lambda line: line == "OK ARM 1")
                # 解锁后不再发送任何命令；仍须自行触发丢帧看门狗。
                receive_until(client, lambda line: line == "FAULT INTERLOCK_OR_TIMEOUT")
    finally:
        bridge.stop()
