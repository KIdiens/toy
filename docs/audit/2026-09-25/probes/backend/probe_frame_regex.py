"""只读审计探针 2：确认 sim.wire.parse_frame 的正则回溯缺陷是否可被
"少一个数字" 的畸形行触发，并产出 **全部四轴都在 1400..1600 内** 的伪有效帧。

判据：
  固件  sscanf("FRAME %lu %d %d %d %d %c") == 5  → 才认为有效
  sim   parse_frame(line) is not None            → 且四轴都落在 [1400,1600] → 才认为有效

若存在一行：固件 rc != 5（拒绝）而 sim 判有效，则 sim 比固件**更宽松** →
主机侧在与 sim 对齐后上真机会走不同分支（危险方向）。
"""
from __future__ import annotations

import ctypes
import itertools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "toy"
sys.path.insert(0, str(REPO))
from sim.wire import parse_frame  # noqa: E402

libc = ctypes.CDLL("msvcrt.dll")
libc.sscanf.restype = ctypes.c_int
LOW, HIGH = 1400, 1600


def c_frame_rc(line: str) -> int:
    seq = ctypes.c_ulong(0)
    a = b = c = d = ctypes.c_int(0)
    extra = ctypes.c_char(b"\x00")
    return libc.sscanf(
        line.encode("ascii"),
        b"FRAME %lu %d %d %d %d %c",
        ctypes.byref(seq), ctypes.byref(a), ctypes.byref(b),
        ctypes.byref(c), ctypes.byref(d), ctypes.byref(extra),
    )


def sim_ok(line: str) -> bool:
    parsed = parse_frame(line)
    if parsed is None:
        return False
    _seq, values = parsed
    return all(LOW <= v <= HIGH for v in values)


# 用会触发"把 8 位数字拆成两个 4 位轴值"的 token 直接构造。
candidates: list[str] = []
for merged in ("14001500", "15001600", "14001400", "16001600", "15001500"):
    for seq in ("0", "7", "12345"):
        for tail in ("1500 1600", "1400 1500", "1600 1400", "1500 1500"):
            candidates.append(f"FRAME {seq} {merged} {tail}")
# 再穷举一些常见畸形组合，防止漏判。
for seq in ("0", "1", "9", "123456789"):
    for parts in itertools.product(["1400", "1500", "1600", "14001500", "15001600", "1", "0"],
                                   repeat=3):
        candidates.append("FRAME " + seq + " " + " ".join(parts))

danger: list[tuple[str, int, object]] = []
for line in candidates:
    fw_ok = c_frame_rc(line) == 5
    s_ok = sim_ok(line)
    if s_ok and not fw_ok:
        danger.append((line, c_frame_rc(line), parse_frame(line)))

print(f"探测 {len(candidates)} 条畸形 FRAME 行")
print(f"发现 sim 判有效、固件拒绝（危险方向）的行数: {len(danger)}")
for line, rc, parsed in danger[:10]:
    print(f"  {line!r}\n     firmware sscanf_rc={rc} (需 ==5)  sim parse={parsed}")

# 单一最小可复现样例
minimal = "FRAME 0 1500 1550 1500"          # 只有 4 个数字
print()
print("最小样例（少一个轴值）：")
print(f"  {minimal!r}")
print(f"  firmware sscanf_rc={c_frame_rc(minimal)} (拒绝)  sim parse={parse_frame(minimal)}")
