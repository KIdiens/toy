"""只读审计探针 5：把解析分歧落到**控制器可观测输出**上（端到端）。

对比对象（固件语义，由探针 1 用真实 C 运行时 sscanf 确认）：
  - "ARM 1 "                  固件 sscanf==1 → 会回 "OK ARM 1"
  - "FRAME 0 1500 1550 1500"  固件 sscanf==4 (≠5) → 会回 "ERR COMMAND"
观察 sim 的真实输出行。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "toy"
sys.path.insert(0, str(REPO))

from sim.clock import SimClock  # noqa: E402
from sim.scenario import settle  # noqa: E402
from sim.tcp_bridge import make_controller  # noqa: E402


def drive(*lines_at: tuple[str, int]) -> list[str]:
    clock = SimClock()
    ctrl = make_controller(clock, role=1, docked=True, estop_ok=True)
    ctrl.power_on()
    ctrl.clear_output()
    for line, at in lines_at:
        clock.advance(max(0, at - clock.now_ms()))
        settle(ctrl, clock)
        ctrl.receive((line + "\n").encode("ascii"))
        settle(ctrl, clock)
    return [f"{t}ms {ln}" for t, ln in ctrl.take_events()]


print("=" * 78)
print("F1. 尾随空白：'ARM 1 '（固件会接受 → OK ARM 1）")
print("=" * 78)
for out in drive(("ARM 1 ", 300)):
    print("   sim>", out)

print()
print("=" * 78)
print("F2. 少一个轴值：'FRAME 0 1500 1550 1500'（固件 → ERR COMMAND）")
print("=" * 78)
for out in drive(("ARM 1", 250), ("FRAME 0 1500 1550 1500", 500)):
    print("   sim>", out)

print()
print("=" * 78)
print("F3. 对照：正常 ARM 1 / 正常 FRAME（基线，确认驱动方式正确）")
print("=" * 78)
for out in drive(("ARM 1", 250), ("FRAME 0 1500 1550 1500 1500", 500)):
    print("   sim>", out)

print("\n完成。")
