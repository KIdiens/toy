"""pyserial 传输：同一份代码对接真机与模拟器。

``serial.serial_for_url`` 原生支持 ``COM5`` / ``/dev/serial/by-id/...`` 以及
``socket://127.0.0.1:5555``（MCU 模拟器的 TCP 服务端）。因此**无需任何
``if sim:`` 分支**（架构 §9.2）。
"""
from __future__ import annotations

import serial  # pyserial==3.5


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

    def open(self) -> None:
        self._ser = serial.serial_for_url(
            self.port,
            baudrate=self.baudrate,
            timeout=self.read_timeout_s,
            write_timeout=self.write_timeout_s,
        )

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def write_line(self, line: str) -> None:
        self._require().write((line + "\n").encode("ascii"))

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
        return lines

    def reset_input_buffer(self) -> None:
        self._require().reset_input_buffer()

    def _require(self) -> serial.Serial:
        if self._ser is None:
            raise RuntimeError("串口未打开：请先调用 open()")
        return self._ser
