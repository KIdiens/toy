"""关闭传输前须给 STOP 回执一个有界接收窗口，避免立即断连丢尾包。"""
from __future__ import annotations

import pytest
from host.core.clock import VirtualClock
from host.motion import serial_link


class DelayedStopAck:
    def __init__(self, clock, mode):
        self.clock, self.mode = clock, mode
        self.ack_read = self.closed = False
        self.closed_after_ack = False

    def write(self, data):
        assert data == b"STOP\n"
        return len(data)

    @property
    def in_waiting(self):
        if self.mode == "broken":
            raise OSError("连接已断开")
        return int(self.mode == "ack" and self.clock.now_ms() >= 30 and not self.ack_read)

    def readline(self):
        self.ack_read = True
        return b"OK STOP\n"

    def close(self):
        self.closed_after_ack = self.ack_read
        self.closed = True


@pytest.mark.parametrize("mode", ["ack", "missing", "broken"])
def test_stop_ack_is_consumed_before_close_with_bounded_failure(monkeypatch, mode):
    clock = VirtualClock()
    # 只替换本模块的时钟，不改全局 time，避免影响测试运行器。
    monkeypatch.setattr(serial_link, "monotonic", lambda: clock.now_ms() / 1000)
    monkeypatch.setattr(serial_link, "sleep", clock.sleep)
    link = serial_link.SerialLink("test-only")
    transport = DelayedStopAck(clock, mode)
    link._ser = transport
    link.write_line("STOP")
    link.close()
    assert transport.closed and link._ser is None
    if mode == "ack":
        assert transport.closed_after_ack, "STOP 回执仍在途中就关闭了连接"
    assert clock.now_ms() <= 120, "无回执时收尾无界等待"
