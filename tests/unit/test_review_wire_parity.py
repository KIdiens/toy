"""第二批整改 · 第 4 组：模拟器与固件 ``sscanf`` 语义一致性。

争议点：``sim/wire.py`` 的正则与真实 C ``sscanf`` 是否等价。

固件（``firmware/motion_controller/motion_controller.ino:55/70``）::

    if (sscanf(line, "ARM %d %c", &expectedRole, &armExtra) == 1) { ... }
    if (sscanf(line, "FRAME %lu %d %d %d %d %c", &seq, &a, &b, &c, &d, &extra) == 5) { ... }

C 标准里，格式串中的**空白指令**匹配零个或多个空白；``%c`` **不跳空白**。
关键结论（本轮实测复核，两条都曾判反）：

1. ``"ARM 1 "`` —— ``%c`` 之前的空白指令吃掉尾随空格后无字符可读，``%c`` 输入失败，
   ``sscanf`` 只完成 ``%d`` 一个赋值，**返回 1 => 真机接受**。
   模拟器此前用 ``\\s*`` 收尾把它拒了，比真机更严。
2. ``"FRAME 1 1500 1500 1500"`` —— 旧正则的 ``\\s*`` 会让 ``\\d+`` 从 ``"1500"`` 的
   ``"150"`` 处起始，凑出 ``[1500, 1500, 150, 0]`` 这种假四轴数据；
   真机上 ``%c`` 之外的三个 ``%d`` 读不满，**返回 3 => 拒绝**。这是纯粹的模拟器 bug。

本文件以「纯 Python 复刻 C ``sscanf`` 赋值计数」为**判定基准**（不拿模拟器自己当 oracle），
逐条比对 14 个 ARM 用例与 12 个 FRAME 用例。

**这个 oracle 本身的强度边界（必须诚实记录）**：它是按 C 标准 7.21.6.2 的赋值计数规则写的
复刻，**不是真实 C 编译器跑出来的结果** —— 本机未安装 gcc/msvc，无法做交叉验证。
所以它能证明「模拟器与 C 标准文本一致」，但**不能**证明「与某个具体编译器的实现一致」。

在补上真机对照之前，下面两条结论的置信度分级如下：

- **高置信**（规则无歧义，业界无实现分歧）：空白指令匹配零个或多个空白；``%c`` 不跳空白；
  字段读不满即返回已完成数。结论 1 与 2 都建立在这三条上。
- **待真机确认**（存在历史实现差异的边缘情形）：``%c`` 遇到输入末尾时的返回值。
  C 标准说「输入项失败」，但部分实现对尾随 ``%c`` 的边界处理曾有差异。
  真机验证方式：烧录后往串口发 ``ARM 1\\r``，看是否回 ``OK``。

若日后在有编译器的机器上复核本文件，只需把 ``_sscanf_count`` 的返回值与真实 ``sscanf``
返回值对拍即可，测试结构无需改动。
"""
from __future__ import annotations

import re

import pytest
from sim import wire

# ------------------------------------------------------------------ C sscanf 判定基准
_WS = re.compile(r"[ \t\n\r\f\v]*")
_INT = re.compile(r"[+-]?\d+")


def _sscanf_count(line: str, spec: str) -> int:
    """复刻 C ``sscanf`` 的赋值字段数（只覆盖本项目用到的 ARM / FRAME 两种格式）。

    规则：
    - 格式串中的空白指令匹配**零个或多个**空白（含尾随空白）；
    - ``%d`` / ``%lu`` 匹配可选符号 + 数字，输入失败即终止；
    - ``%c`` 匹配**恰好一个字符**，不跳空白（空白由前置指令处理）；
    - 返回成功赋值的字段数。
    """
    pos = 0
    assigned = 0
    i = 0
    while i < len(spec):
        ch = spec[i]
        if ch.isspace():
            m = _WS.match(line, pos)
            pos = m.end()
            i += 1
            continue
        if ch == "%":
            # 跳过长度修饰符（l / ll / h），本项目只用到 %lu 与 %d
            i += 1
            while i < len(spec) and spec[i] in "hljztL":
                i += 1
            kind = spec[i]
            if kind == "c":
                if pos >= len(line):
                    return assigned  # 输入失败：%c 无字符可读
                pos += 1            # %c 只吃一个字符
            elif kind in ("d", "u"):
                m = _INT.match(line, pos)
                if not m:
                    return assigned  # 输入失败
                pos = m.end()
            else:
                raise ValueError(f"未支持的转换 %{kind}")
            assigned += 1
            i += 1
            continue
        # 字面字符必须精确匹配
        if pos >= len(line) or line[pos] != ch:
            return assigned
        pos += 1
        i += 1
    return assigned


def c_accepts_arm(line: str) -> bool:
    return _sscanf_count(line, "ARM %d %c") == 1


def c_accepts_frame(line: str) -> bool:
    return _sscanf_count(line, "FRAME %lu %d %d %d %d %c") == 5


# ------------------------------------------------------------------ 一致性矩阵
ARMS = [
    "ARM 1",        # 基准：真机接受
    "ARM1",         # 格式串空白匹配零个 -> 接受
    "ARM  1",       # 多个空白 -> 接受
    "ARM 1 ",       # 尾随空白：%c 输入失败 -> sscanf 返回 1 -> **接受**
    "ARM 1  ",      # 多个尾随空白 -> 接受
    "ARM 1 2",      # 尾随 token -> %c 成功 -> 返回 2 -> 拒绝
    "ARM 1x",       # %c 紧邻吃掉 'x' -> 返回 2 -> 拒绝
    "ARM 1a",       # 同上，无空格 -> 拒绝
    "ARM -1",       # 负数：%d 接受（角色号语义交给固件判联锁）
    "ARM +1",       # 显式正号 -> 接受
    "ARM",          # 缺 %d -> 拒绝
    "ARM ",         # 只有空白 -> 拒绝
    "ARM 01",       # 前导零 -> %d 接受 -> 接受
    "arm 1",        # 大小写敏感：字面 "ARM" 不匹配 'a' -> 拒绝
    # --- C 空白指令吃 isspace() 全集，不只是水平空白 ---
    "ARM 1\x0b",    # 尾随 VT -> %c 失败 -> 返回 1 -> 接受
    "ARM 1\x0c",    # 尾随 FF -> 接受
    "ARM\x0b1",     # 首部空白指令吃 VT -> 接受
    "ARM\x0b\x0c1", # 多个 C 空白 -> 接受
    "ARM 1\x0b 2",  # VT 后跟 token -> %c 成功 -> 返回 2 -> 拒绝
]

FRAMES = [
    "FRAME 1 1500 1500 1500 1500",              # 基准
    "FRAME1 1500 1500 1500 1500",               # 首部空白指令吃零个
    "FRAME  1  1500 1500 1500 1500",            # 多空白
    "FRAME 1 1500 1500 1500 1500 ",             # 尾随空白 -> %c 失败 -> 返回 5 -> 接受
    "FRAME 1 1500 1500 1500 1500  ",            # 多尾随空白 -> 接受
    "FRAME 1 1500 1500 1500 1500 x",            # 尾随 token -> 返回 6 -> 拒绝
    "FRAME 1 1500 1500 1500",                   # 少一个轴 -> 拒绝（且不得截断凑数）
    "FRAME 1 1500 1500",                        # 少两个轴 -> 拒绝
    "FRAME 1 1500 1500 1500 1500 1500",         # 多一个轴 -> 拒绝
    "FRAME 4294967295 1500 1500 1500 1500",     # %lu 上界 -> 接受
    "FRAME 4294967296 1500 1500 1500 1500",     # %lu 回绕 -> 接受
    "frame 1 1500 1500 1500 1500",              # 大小写 -> 拒绝
    # --- 字段间也是 C 空白指令：VT/FF 同样被吃掉 ---
    "FRAME 1\x0b1500 1500 1500 1500",           # VT 分隔第一个字段 -> 接受
    "FRAME 1 1500\x0c1500 1500 1500",           # FF 分隔 -> 接受
    "FRAME 1 1500 1500 1500 1500\x0b",          # 尾随 VT -> %c 失败 -> 返回 5 -> 接受
]


@pytest.mark.parametrize("line", ARMS)
def test_parse_arm_matches_c_sscanf(line: str) -> None:
    """parse_arm 必须与 C ``sscanf(line, "ARM %d %c") == 1`` 逐条一致。"""
    expected = c_accepts_arm(line)
    got = wire.parse_arm(line) is not None
    assert got == expected, (
        f"{line!r}：模拟器 {'接受' if got else '拒绝'}，"
        f"真机 sscanf {'接受' if expected else '拒绝'} —— 语义不一致"
    )


@pytest.mark.parametrize("line", FRAMES)
def test_parse_frame_matches_c_sscanf(line: str) -> None:
    """parse_frame 必须与 C ``sscanf(...) == 5`` 逐条一致（只比对接受/拒绝）。"""
    expected = c_accepts_frame(line)
    got = wire.parse_frame(line) is not None
    assert got == expected, (
        f"{line!r}：模拟器 {'接受' if got else '拒绝'}，"
        f"真机 sscanf {'接受' if expected else '拒绝'} —— 语义不一致"
    )


# ------------------------------------------------------------------ 已知分歧（已修，钉死）
def test_trailing_whitespace_is_accepted_like_c() -> None:
    """固件级事实：尾随空白被 ``%c`` 前的空白指令吃掉，``%c`` 输入失败 → 命令**被接受**。

    这条曾被写成相反的期望（"尾随空格应 ERR COMMAND"），那是在保护错误行为：
    真实 C 运行时的 ``sscanf("ARM %d %c")`` 对 ``"ARM 1 "`` 返回 1，
    真机因此会接受该行。模拟器此前比真机更严，会在联调期给出与真机相反的结论。
    """
    assert wire.parse_arm("ARM 1 ") == 1
    assert wire.parse_arm("ARM 1  ") == 1
    result = wire.parse_frame("FRAME 1 1500 1500 1500 1500 ")
    assert result is not None and result[1] == [1500, 1500, 1500, 1500]


def test_trailing_token_is_rejected() -> None:
    """尾随非空白字符时 ``%c`` 成功、返回 N+1 —— 必须拒绝。"""
    assert wire.parse_arm("ARM 1 2") is None
    assert wire.parse_arm("ARM 1x") is None
    assert wire.parse_frame("FRAME 1 1500 1500 1500 1500 x") is None
    assert wire.parse_frame("FRAME 1 1500 1500 1500 1500x") is None


def test_no_digit_truncation_on_short_field_lists() -> None:
    """回归：字段数不足时不得在数字中间截断凑出假数据。

    旧正则 ``\\s*`` 会让 ``\\d+`` 从 ``"1500"`` 的 ``"150"`` 处起始，
    ``"FRAME 1 1500 1500 1500"`` 被解析成 ``(1, [1500, 1500, 150, 0])``
    —— 一个真机会直接 ERR FRAME 的行，在模拟器里变成了"合法四轴数据"。
    """
    for line in ("FRAME 1 1500 1500 1500", "FRAME 1 1500 1500", "FRAME 1 1500"):
        assert wire.parse_frame(line) is None, f"{line!r} 不该被接受"


def test_arm_and_frame_disjoint_on_trailing_token() -> None:
    """带尾随 token 的行不能被 ARM 误吞，也不能被 FRAME 误吞。"""
    assert wire.parse_arm("ARM 1 2") is None
    assert wire.parse_frame("ARM 1 2") is None


def test_frame_seq_wraps_like_unsigned_long() -> None:
    """%lu 语义：32 位无符号回绕。"""
    result = wire.parse_frame("FRAME 4294967295 1500 1500 1500 1500")
    assert result is not None
    assert result[0] == 0xFFFFFFFF
    wrapped = wire.parse_frame("FRAME 4294967296 1500 1500 1500 1500")
    assert wrapped is not None and wrapped[0] == 0
