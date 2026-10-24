"""pyserial 传输：同一份代码对接真机与模拟器。

``serial.serial_for_url`` 原生支持 ``COM5`` / ``/dev/serial/by-id/...`` 以及
``socket://127.0.0.1:5555``（MCU 模拟器的 TCP 服务端）。因此**无需任何
``if sim:`` 分支**（架构 §9.2）。
"""
from __future__ import annotations

import logging
from time import monotonic, sleep

import serial  # pyserial==3.5

STOP_ACK_TIMEOUT_S = 0.1


class SerialLink:
    """基于 pyserial 的 ``MotionLink`` 实现。"""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        read_timeout_s: float = 0.02,
        write_timeout_s: float = 0.2,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.read_timeout_s = read_timeout_s
        self.write_timeout_s = write_timeout_s
        self._ser: serial.Serial | None = None
        self._stop_pending = False

    def open(self) -> None:
        self._ser = serial.serial_for_url(
            self.port,
            baudrate=self.baudrate,
            timeout=self.read_timeout_s,
            write_timeout=self.write_timeout_s,
        )

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._await_stop_ack()
            finally:
                self._ser.close()
                self._ser = None
                self._stop_pending = False

    def _await_stop_ack(self) -> None:
        """给已发送的 STOP 一个有界回执窗口；避免 TCP 立即断连丢尾包。

        只等既有 OK STOP，不重发 ARM 或新增命令。无回执/断线仍释放传输，
        记日志而不冒充确认成功；不替代实机急停与看门狗验收。
        """
        if not self._stop_pending:
            return
        deadline = monotonic() + STOP_ACK_TIMEOUT_S
        try:
            while self._stop_pending and monotonic() < deadline:
                self.read_lines()
                if self._stop_pending:
                    sleep(min(0.005, max(0, deadline - monotonic())))
        except OSError as exc:
            logging.getLogger(__name__).warning("STOP 回执前连接异常，继续关闭：%s", exc)
            return
        if self._stop_pending:
            logging.getLogger(__name__).warning("未收到 STOP 回执，继续关闭连接")

    def write_line(self, line: str) -> None:
        self._require().write((line + "\n").encode("ascii"))
        self._stop_pending = line == "STOP"

    def read_lines(self) -> list[str]:
        ser = self._require()
        lines: list[str] = []
        while ser.in_waiting:
            raw = ser.readline()
            if not raw:
                break
            text = raw.decode("ascii", errors="replace").strip()
            if text:
                lines.append(text)
                if text == "OK STOP":
                    self._stop_pending = False
        return lines

    def reset_input_buffer(self) -> None:
        self._require().reset_input_buffer()

    def _require(self) -> serial.Serial:
        if self._ser is None:
            raise RuntimeError("串口未打开：请先调用 open()")
        return self._ser
