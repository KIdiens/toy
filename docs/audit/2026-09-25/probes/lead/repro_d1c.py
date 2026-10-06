"""D1 因果实验：`motion_busy` 是不是「表演键卡死」的那道守卫。

假设：`pump_once` 见 `motion_busy=False` 就把 PLAY_ACTION 当陈旧命令丢弃。
那么只改这一个变量（按键前预置 motion_busy=True），表演键就应当能正常收尾回 IDLE。
控制其余一切不变（同一装置、同一按键、同一时刻序列）。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402

SEP = "=" * 76
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-d1c-", dir=r"F:/AI智能底座玩具/_audit_lead"))


def run(*, preset_busy: bool) -> tuple[str, bool]:
    settings = load_settings({"DB_PATH": str(SCRATCH / f"d1c-{preset_busy}.sqlite3"),
                              "MOTION_BOOT_WAIT_S": "0.05"})
    ctx = build_context(settings, simulate=True, root=TOY_ROOT)
    stop = threading.Event()
    ctx.worker_running = True
    ctx.start_workers()
    threading.Thread(target=ctx.run_forever, args=(stop,), daemon=True).start()
    time.sleep(0.2)
    try:
        if preset_busy:
            ctx.motion_busy = True          # ← 唯一被改动的自变量
        ctx.press_button(2)
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            if ctx.state_machine.state.value == "IDLE":
                break
            time.sleep(0.02)
        state = ctx.state_machine.state.value
        recovered = state == "IDLE"
        label = "预置 motion_busy=True " if preset_busy else "不预置（生产实况）"
        print(f"  {label} → 按键后最终状态 = {state:12s} "
              f"{'收尾回 IDLE' if recovered else '← 卡在 PERFORMING'}")
        print(f"     迁移历史 = "
              f"{[(r.source.value, r.trigger.value, r.dest.value) for r in ctx.state_machine.history][-3:]}")
        return state, recovered
    finally:
        stop.set()
        ctx.close()


def main() -> int:
    print(SEP)
    print("D1-c  单变量对照：motion_busy 是否是那道守卫")
    print(SEP)
    _s1, recovered_without = run(preset_busy=False)
    _s2, recovered_with = run(preset_busy=True)
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  不预置（生产实况）      收尾 = {recovered_without}")
    print(f"  预置 motion_busy=True  收尾 = {recovered_with}")
    causal = (not recovered_without) and recovered_with
    print(f"  → 单变量即翻转结果 = {causal}"
          f"   {'（根因锁定为 pump_once 的 motion_busy 守卫）' if causal else '（假设不成立，需另找根因）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
