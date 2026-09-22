"""字节流协议层：逐字复刻 ``motion_controller.ino`` 的行缓冲语义。

固件依据（``loop()`` 内的串口段）::

    char buffer[100];
    size_t used = 0;
    bool overflowLine = false;
    ...
    for (int n = 0; n < 64 && Serial.available(); ++n) {
      char ch = Serial.read();
      if (ch == '\r') continue;                       // 回车一律丢弃，不计长度
      if (ch == '\n') {
        if (!overflowLine) { buffer[used] = 0; command(buffer); }
        else { stopMotion(); Serial.println("ERR LENGTH"); }
        used = 0; overflowLine = false;
      } else if (used < sizeof(buffer) - 1 && !overflowLine) buffer[used++] = ch;
      else overflowLine = true;                        // 第 100 个字符才置溢出
    }

结论（本模块负责的**唯一**语义）:

- 行上限 99 字符（``sizeof(buffer)-1``）；第 100 个字符起该行判溢出。
- ``\\r`` 无条件跳过，且不占长度预算。
- 溢出行在遇到 ``\\n`` 时给出 ``overflow=True``，由调用方停机并回 ``ERR LENGTH``。
- 每循环 64 字节的预算**不在此处**：它对应固件 ``for (n < 64 ...)`` 的循环上限，
  由 ``controller.loop_once`` 从 RX 队列每次取至多 64 字节后调用 ``feed``。

本模块不发命令、不碰状态机，只做缓冲、分帧与命令解析（sscanf 等价判定）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CR = 0x0D
LF = 0x0A
BUFFER_SIZE = 100
MAX_LINE_CHARS = BUFFER_SIZE - 1
BYTES_PER_CYCLE = 64
SEQ_MASK = 0xFFFFFFFF

# 与固件 sscanf 等价的解析（%d 之前的格式串空白匹配零或多个空白）。
# 关键细节：``sscanf("ARM %d %c") == 1`` 要求 **无尾随字符** —— 任何尾随字符
# （包括空格）都会让赋值字段数变成 2，从而落入 FRAME 分支失败 → ERR COMMAND。
_ARM_RE = re.compile(r"^ARM\s*([+-]?\d+)$")
_FRAME_RE = re.compile(
    r"^FRAME\s*([+-]?\d+)\s*([+-]?\d+)\s*([+-]?\d+)\s*([+-]?\d+)\s*([+-]?\d+)$"
)


def parse_arm(line: str) -> int | None:
    """等价 ``sscanf(line, "ARM %d %c") == 1``；命中返回角色号，否则 ``None``。"""
    match = _ARM_RE.match(line)
    return int(match.group(1)) if match else None


def parse_frame(line: str) -> tuple[int, list[int]] | None:
    """等价 ``sscanf(line, "FRAME %lu %d %d %d %d %c") == 5``；返回 (seq, 四轴)。"""
    match = _FRAME_RE.match(line)
    if not match:
        return None
    seq = int(match.group(1)) & SEQ_MASK  # %lu 语义：32 位无符号回绕
    values = [int(match.group(index)) for index in range(2, 6)]
    return seq, values


@dataclass(frozen=True, slots=True)
class LineEvent:
    """一次断行结果：``line`` 为完整行；``overflow`` 为真表示该行溢出。"""

    line: str
    overflow: bool


class Wire:
    """行缓冲：与固件同名的 ``used`` / ``overflowLine`` 语义。"""

    def __init__(self, *, buffer_size: int = BUFFER_SIZE) -> None:
        if buffer_size < 2:
            raise ValueError("buffer_size 至少为 2")
        self._buffer = bytearray()
        self._overflow = False
        self._line_limit = buffer_size - 1

    @property
    def pending(self) -> int:
        """当前半行已缓存字符数（不含溢出标志）。"""
        return len(self._buffer)

    @property
    def overflowed(self) -> bool:
        return self._overflow

    def feed(self, data: bytes) -> list[LineEvent]:
        """消费一段字节，返回本段内完成的每一个整行（可 0 个或多个）。"""
        events: list[LineEvent] = []
        for byte in data:
            if byte == CR:
                continue
            if byte == LF:
                if self._overflow:
                    events.append(LineEvent("", True))
                else:
                    events.append(
                        LineEvent(self._buffer.decode("ascii", "replace"), False)
                    )
                self._buffer.clear()
                self._overflow = False
                continue
            if not self._overflow and len(self._buffer) < self._line_limit:
                self._buffer.append(byte)
            else:
                self._overflow = True
        return events

    def reset(self) -> None:
        """丢弃半行与溢出标志（不产生任何输出）。"""
        self._buffer.clear()
        self._overflow = False
