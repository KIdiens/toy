"""独立复现 QA-3-2 的 D1：表演键（button 2）使设备永久卡在 PERFORMING。

对照实验设计：同一个装置里先走 HTTP play_action（已知能跑通），再走按键路径，
两者唯一的差别就是「谁置了 motion_busy」，以此把根因钉死。

运行：python _audit_lead/repro_qa3.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402

SEP = "=" * 76
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-qa3-", dir=r"F:/AI智能底座玩具/_audit_lead"))


def _ctx(name: str):
    settings = load_settings({"DB_PATH": str(SCRATCH / f"{name}.sqlite3"),
                              "MOTION_BOOT_WAIT_S": "0.05"})
    return build_context(settings, simulate=True, root=TOY_ROOT)


def _start_full(ctx):
    """**完整**装配：main.py 里 motion worker 是单独起的，start_workers() 只管 net/audio/tick。

    漏掉 motion worker 会让运动命令永远无人消费 —— 第一版探针就栽在这（对照组 seq 恒为 0），
    于是「卡住」看起来像复现，其实是装置没跑起来。
    """
    import threading

    stop = threading.Event()
    ctx.worker_running = True
    ctx.start_workers()  # net / audio / tick
    motion_thread = threading.Thread(target=ctx.run_forever, args=(stop,),
                                     name="probe-motion", daemon=True)
    motion_thread.start()
    time.sleep(0.2)  # 让 worker 进入循环
    return stop, motion_thread


def _watch(ctx, seconds: float) -> list[str]:
    seen: list[str] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = ctx.state_machine.state.value
        if not seen or seen[-1] != state:
            seen.append(state)
        time.sleep(0.02)
    return seen


def d1_button_perform() -> bool:
    print(SEP)
    print("D1-a  物理表演键（button 2）→ 是否卡在 PERFORMING")
    print(SEP)
    ctx = _ctx("d1")
    stop, _thread = _start_full(ctx)
    try:
        print(f"  初始状态 = {ctx.state_machine.state.value}")
        ctx.press_button(2)
        print(f"  按键后状态 = {ctx.state_machine.state.value}"
              f"   motion_busy={ctx.motion_busy}")
        seq = _watch(ctx, 2.5)
        print(f"  后续 2.5s 状态序列 = {seq}")
        stuck = seq[-1] == "PERFORMING"
        print(f"  → 是否卡住 = {stuck}")

        # 第二次按下能否自救
        ctx.press_button(2)
        print(f"  再按一次后状态 = {ctx.state_machine.state.value}")
        print(f"  该状态迁移历史 = "
              f"{[(r.source.value, r.trigger.value, r.dest.value) for r in ctx.state_machine.history]}")
        return stuck
    finally:
        ctx.close()


def d1_contrast_http() -> None:
    print()
    print(SEP)
    print("D1-b  对照：同一装置走 HTTP play_action（唯一差别是谁置 motion_busy）")
    print(SEP)
    ctx = _ctx("d1b")
    stop, _thread = _start_full(ctx)
    try:
        result = ctx.play_action(role_id=1, action_id="wave")
        print(f"  play_action 返回 = {result}")
        print(f"  调用后 motion_busy = {ctx.motion_busy}")
        seq = _watch(ctx, 3.0)
        print(f"  后续 3.0s 状态序列 = {seq}")
        print(f"  → 是否正常收尾回 IDLE = {seq[-1] == 'IDLE'}")
        print(f"  动作进度 = {ctx.action_progress}")
    finally:
        ctx.close()


def main() -> int:
    stuck = d1_button_perform()
    d1_contrast_http()
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  D1 物理表演键卡死 : {'复现（确定缺陷）' if stuck else '未复现'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
