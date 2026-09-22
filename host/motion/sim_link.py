"""进程内传输：直接持有模拟器 ``McController`` 并注入虚拟时钟。

与 ``software/test_protocol_simulation.py::FakeSerial`` 的关键区别（这是**改进**，
不改变对外行为）：本实现驱动的是**真固件等价状态机**（含看门狗、稳定期、PWM
步进），而非预设字符串；且通过 DI 注入 Clock，不再 monkeypatch 模块。

时基同步：``open()`` 时把 ``controller.run_until`` 注册为虚拟时钟监听器，于是主机
侧的 ``clock.sleep`` 会同步推进 MCU 的 ``millis()``，让 200ms 稳定期、200ms 报告
间隔、300ms 看门狗都能在零真实等待下复现。
"""
from __future__ import annotations

from host.motion.link import MotionLink  # noqa: F401  （用于类型对照，运行时无需）

_SYNC_LIMIT = 4096


class SimLink:
    """进程内 ``MotionLink``；用于单元/集成测试与 ``--simulate`` 开发档位。"""

    def __init__(self, controller, *, clock=None) -> None:
        self.controller = controller
        self.clock = clock if clock is not None else controller.clock
        self._listening = False

    # ---------- MotionLink ----------
    def open(self) -> None:
        self.controller.power_on()
        add = getattr(self.clock, "add_listener", None)
        if callable(add):
            add(self._on_advance)
            self._listening = True
        self._sync()

    def close(self) -> None:
        remove = getattr(self.clock, "remove_listener", None)
        if self._listening and callable(remove):
            remove(self._on_advance)
            self._listening = False

    def write_line(self, line: str) -> None:
        self.controller.receive((line + "\n").encode("ascii"))
        self._sync()

    def read_lines(self) -> list[str]:
        self._sync()
        return [line for line in self.controller.take_output() if line]

    def reset_input_buffer(self) -> None:
        self.controller.clear_output()

    # ---------- 内部 ----------
    def _on_advance(self, now_ms: int) -> None:
        self.controller.run_until(now_ms)

    def _sync(self) -> None:
        """推进到当前时刻，并把 RX 里积压的整行按 64B/圈预算消费完。"""
        self.controller.run_until(self.clock.now_ms())
        guard = 0
        while self.controller.has_pending_input():
            self.controller.poll()
            guard += 1
            if guard > _SYNC_LIMIT:
                raise RuntimeError("SimLink 的 RX 队列未能清空（协议异常）")
