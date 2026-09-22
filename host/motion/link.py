"""串口抽象：``MotionLink`` Protocol。

三种实现互换（架构 §9.2）：``SerialLink``（真串口 + socket:// 模拟器）、
``SimLink``（进程内模拟器）、``ReplayLink``（录制回放）。生产只 import SerialLink。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MotionLink(Protocol):
    """最小串口契约：写整行、非阻塞取回整行。"""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_line(self, line: str) -> None:
        """写入一行（实现负责追加 ``\\n``）。"""

    def read_lines(self) -> list[str]:
        """非阻塞取回**已到达**的整行（可能为空列表）。"""

    def reset_input_buffer(self) -> None:
        """丢弃尚未读取的设备→主机数据。"""
