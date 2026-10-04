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

# 与固件 sscanf 等价的解析。**判定基准是 C 的格式串语义，不是"看起来像"**：
#
# C 里格式串的空白指令匹配**零个或多个**空白，且 ``%c`` 之前的空白指令会吃掉
# 尾随空白 —— 于是 ``"ARM 1 "`` 的 ``%c`` 无字符可读、输入失败，``sscanf`` 返回 1
# （只有 ``%d`` 完成赋值）=> **真机接受**。这一点必须与固件一致。
#
# 同理 ``%c`` 不跳空白但也不能凭空接受：只有当 ``%d`` 之后**确实还有非空白字符**
# 时它才成功（返回 2），否则整条命令落到 ERR COMMAND。
#
# 依据是 C 标准 7.21.6.2 的赋值计数规则，**非**本机 C 编译器实测（工程机无 gcc/msvc）。
# 真机复核方式：烧录后发一字节 ``ARM 1\r``，固件应回 ``OK``（与本模块判定一致）。
# 详见 tests/unit/test_review_wire_parity.py 文件头的置信度分级。
#
# 空白指令要吃 C 的 ``isspace()`` 全集：space / ``\t`` / ``\v`` / ``\f`` / ``\r`` / ``\n``。
# **不能**用 ``\s*`` —— 那会额外吃掉 ``\n``，而本模块的 ``feed()`` 已按 ``\n`` 断行
# （与固件 motion_controller.ino:109-110 一致），混入换行只会造成误判。
# 但也不能只用 ``[ \t]*``：固件只丢 ``\r``、只按 ``\n`` 断行，**``\v``/``\f`` 会原样进
# buffer**，而真机的空白指令会吃掉它们 —— 只认水平空白会让模拟器比真机更严
# （``"ARM 1\x0b"`` 真机 sscanf 返回 1，模拟器却回 ERR COMMAND）。
# 故显式列出除 ``\n`` 外的全部 C 空白。
_WS_CHARS = " \t\v\f\r"
_WS_CLASS = r"[ \t\v\f\r]*"
_ARM_SPACE = _WS_CLASS
_INT = r"[+-]?[0-9]+"

# 每个数字字段后必须紧跟"行尾或空白"：``(?![0-9])`` 保证不会在数字中间截断
# （旧正则在字段数不足时会让 \d+ 从 "1500" 的 "150" 处起始，凑出 [150,0] 这类假数据），
# 随后的 ``_ARM_SPACE`` 把该处空白吃掉，交给 _trailing_is_clean 判定 %c。
_ARM_RE = re.compile(rf"^ARM{_ARM_SPACE}({_INT})(?![0-9])")
_FRAME_RE = re.compile(
    rf"^FRAME{_ARM_SPACE}({_INT})(?![0-9]){_ARM_SPACE}"
    rf"({_INT})(?![0-9]){_ARM_SPACE}"
    rf"({_INT})(?![0-9]){_ARM_SPACE}"
    rf"({_INT})(?![0-9]){_ARM_SPACE}"
    rf"({_INT})(?![0-9])"
)


def _trailing_is_clean(line: str, end: int) -> bool:
    """字段读完位置之后只允许 C 空白或行尾。

    这是 ``sscanf(...) == N`` 的最后一环：``%c`` 成功则返回 N+1（命令被拒），
    失败（只剩空白或行尾）才返回 N（命令被接受）。
    """
    return line[end:].strip(_WS_CHARS) == ""


def parse_arm(line: str) -> int | None:
    """等价 ``sscanf(line, "ARM %d %c") == 1``；命中返回角色号，否则 ``None``。

    尾随空白（``"ARM 1 "``）**必须接受**：``%c`` 前的空白指令吃掉它之后无字符可读，
    ``sscanf`` 返回 1。尾随非空白字符（``"ARM 1 2"``）必须拒绝：``%c`` 成功，返回 2。
    """
    match = _ARM_RE.match(line)
    if not match:
        return None
    if not _trailing_is_clean(line, match.end()):
        return None  # %c 读到了字符 -> 返回 2 -> 落到 ERR COMMAND
    return int(match.group(1))


def parse_frame(line: str) -> tuple[int, list[int]] | None:
    """等价 ``sscanf(line, "FRAME %lu %d %d %d %d %c") == 5``；返回 (seq, 四轴)。"""
    match = _FRAME_RE.match(line)
    if not match:
        return None
    if not _trailing_is_clean(line, match.end()):
        return None  # %c 成功 -> 返回 6 -> ERR COMMAND
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
