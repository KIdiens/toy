"""TCP 服务端：把 MCU 模拟器暴露成 ``socket://``，供 pyserial 原生连接。

关键收益（架构 §9.2）：``host/motion/serial_link.py`` 无需任何 ``if sim:`` 分支 ——
同一份串口代码用 ``socket://127.0.0.1:5555`` 对接模拟器，用 ``COM5`` /
``/dev/serial/by-id/...`` 对接真机。

每接受一个连接 = 一台独立的 MCU（各自的控制器与真实单调时钟）；连接断开即
销毁，可反复断连重连（覆盖「通信中断恢复」）。
"""
from __future__ import annotations

import contextlib
import socket
import threading
import time

from sim.clock import WallClock
from sim.controller import McController
from sim.devices import Buttons, Dock, Estop, RoleResistor


def make_controller(clock, *, pwm_ready=True, role=1, docked=True, estop_ok=True):
    """构造一台带指定初始物理状态的控制器（供 CLI 与测试复用）。"""
    return McController(
        clock,
        pwm_ready=pwm_ready,
        dock=Dock(engaged=docked),
        estop=Estop(pressed=not estop_ok),
        role=RoleResistor(role=role),
        buttons=Buttons(),
    )


class TcpBridge:
    """阻塞式 accept 循环 + 每连接一个守护线程。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 5555,
        controller_factory=None,
        tick_interval: float = 0.005,
    ) -> None:
        self._host = host
        self._requested_port = int(port)
        self._controller_factory = controller_factory or (lambda clock: McController(clock))
        self._tick_interval = float(tick_interval)
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.port = self._requested_port

    def _bind(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._requested_port))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]

    def start(self) -> TcpBridge:
        """绑定端口并在后台线程服务（测试用）。"""
        self._bind()
        self._thread = threading.Thread(target=self._serve, name="sim-tcp", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        """前台服务（CLI 用）。"""
        self._bind()
        self._serve()

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(conn,), name="sim-tcp-conn", daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        clock = WallClock()
        controller = self._controller_factory(clock)
        controller.power_on()
        conn.setblocking(False)
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except BlockingIOError:
                    data = None
                except OSError:
                    break
                if data == b"":
                    break  # 对端关闭
                if data:
                    controller.receive(data)
                    controller.run_until(clock.now_ms())
                out = controller.take_output()
                if out:
                    try:
                        conn.sendall(("\n".join(out) + "\n").encode("ascii"))
                    except OSError:
                        break
                time.sleep(self._tick_interval)
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
