"""只读审计探针：用系统 C 运行时真调 sscanf，核对 sim 的解析是否等价于固件。

不修改仓库任何文件。目的：证伪/证实
  firmware:  sscanf(line, "ARM %d %c", &r, &extra) == 1
  firmware:  sscanf(line, "FRAME %lu %d %d %d %d %c", ...) == 5
与
  sim.wire.parse_arm / parse_frame
在「尾随空白」等边界上的分歧。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "toy"
sys.path.insert(0, str(REPO))

from sim.wire import parse_arm, parse_frame  # noqa: E402

libc = ctypes.CDLL("msvcrt.dll")
libc.sscanf.restype = ctypes.c_int


def _c_arm(line: str) -> int:
    r = ctypes.c_int(0)
    extra = ctypes.c_char(b"\x00")
    n = libc.sscanf(
        line.encode("ascii"),
        b"ARM %d %c",
        ctypes.byref(r),
        ctypes.byref(extra),
    )
    return n


def _c_frame(line: str) -> int:
    seq = ctypes.c_ulong(0)
    a = b = c = d = ctypes.c_int(0)
    extra = ctypes.c_char(b"\x00")
    n = libc.sscanf(
        line.encode("ascii"),
        b"FRAME %lu %d %d %d %d %c",
        ctypes.byref(seq),
        ctypes.byref(a),
        ctypes.byref(b),
        ctypes.byref(c),
        ctypes.byref(d),
        ctypes.byref(extra),
    )
    return n


ARM_CASES = [
    "ARM 1",
    "ARM 1 ",
    "ARM 1  ",
    "ARM 1\t",
    "ARM 1 2",
    "ARM 1 x",
    "ARM1",
    "ARM 12",
    "ARM  12",
    "ARM +1",
    "ARM -1",
    "ARM",
    "ARM ",
]

FRAME_CASES = [
    "FRAME 0 1500 1550 1500 1500",
    "FRAME 0 1500 1550 1500 1500 ",
    "FRAME 0 1500 1550 1500 1500  ",
    "FRAME 0 1500 1550 1500 1500\t",
    "FRAME 0 1500 1550 1500 1500 9",
    "FRAME 0 1500 1550 1500",
    "FRAME 0 1500 1550 1500 1500 x",
    "FRAME 0 1500 1550 1500 1600",
    "FRAME -1 1500 1550 1500 1500",
]

print("=== ARM: firmware(sscanf==1) vs sim(parse_arm is not None) ===")
mismatch = 0
for case in ARM_CASES:
    fw = _c_arm(case) == 1
    sim = parse_arm(case) is not None
    flag = "" if fw == sim else "   <<< MISMATCH"
    if fw != sim:
        mismatch += 1
    print(f"{case!r:24} fw_ok={fw!s:5} sim_ok={sim!s:5} sscanf_rc={_c_arm(case)}{flag}")

print()
print("=== FRAME: firmware(sscanf==5) vs sim(parse_frame is not None) ===")
for case in FRAME_CASES:
    fw = _c_frame(case) == 5
    sim = parse_frame(case) is not None
    flag = "" if fw == sim else "   <<< MISMATCH"
    if fw != sim:
        mismatch += 1
    print(f"{case!r:40} fw_ok={fw!s:5} sim_ok={sim!s:5} sscanf_rc={_c_frame(case)}{flag}")

print()
print(f"TOTAL MISMATCHES: {mismatch}")
