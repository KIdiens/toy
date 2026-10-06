"""独立复现架构师报告的 F1 / F2 两项阻断（真模块、真 AppContext，非构造）。

F1: Engine.apply 的 EXIT 命令携带「迁移后」代数 → 取消录音时凭空多录一段
F2: play_action 在「返回 409」的路径上已经改了状态机（拒绝请求有副作用）

运行：python _audit_lead/repro_arch.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.adapters.queues import CommandRouter  # noqa: E402
from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402
from host.core.bus import EventBus  # noqa: E402
from host.core.engine import Engine  # noqa: E402
from host.core.errors import ConflictError  # noqa: E402
from host.core.state_machine import SessionStateMachine, State, Trigger  # noqa: E402

SEP = "=" * 74
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-arch-", dir=r"F:/AI智能底座玩具/_audit_lead"))


def _ctx(name: str):
    settings = load_settings({"DB_PATH": str(SCRATCH / f"{name}.sqlite3"), "MOTION_BOOT_WAIT_S": "0.05"})
    return build_context(settings, simulate=True, root=TOY_ROOT)


# ------------------------------------------------------------------ F1 第 1 部分
def f1_generations() -> None:
    print(SEP)
    print("F1-a  EXIT 命令携带的代数（裸引擎，可逐条读命令）")
    print(SEP)
    for label, initial, sequence in (
        ("正常结束  录音中按对话键", State.RECORDING, [Trigger.REC_STOP]),
        ("取消录音  录音中按表演键", State.RECORDING, [Trigger.REC_CANCEL]),
    ):
        machine = SessionStateMachine(initial=State.IDLE, generation=5)
        router = CommandRouter()
        engine = Engine(EventBus(), router, state_machine=machine)

        # 注意：Engine.apply() 是「返回」命令列表，不写队列（写队列的是 step()/dispatcher）。
        start_cmds = engine.apply(Trigger.DIALOG_PRESS)  # IDLE -> RECORDING（gen 不变）
        start = [c for c in start_cmds if c.kind.value == "record_start"]
        started_gen = start[0].generation if start else None

        for trigger in sequence:
            sequence_cmds = engine.apply(trigger)
        stops = [c for c in sequence_cmds if c.kind.value == "record_stop"]
        stop_gen = stops[0].generation if stops else None

        print(f"  {label}")
        print(f"    录音起始代数 RECORD_START.gen = {started_gen}")
        print(f"    收尾命令   RECORD_STOP.gen  = {stop_gen}")
        print(f"    _finish_recording 判定 done  = {stop_gen == started_gen}"
              f"   {'（一致，不触发兜底录制）' if stop_gen == started_gen else '（不一致 → 走兜底 _record_once，凭空多录一段）'}")


# ------------------------------------------------------------------ F1 第 2 部分
def f1_actual_records() -> None:
    print()
    print(SEP)
    print("F1-b  真实 AppContext + 真实 audio worker：取消录音会不会真的多录一段")
    print(SEP)
    ctx = _ctx("f1")
    calls: list[int] = []
    original = ctx.recorder.record

    def counting(*args, **kwargs):
        calls.append(ctx.state_machine.generation)
        return original(*args, **kwargs)

    ctx.recorder.record = counting
    ctx.start_workers()
    try:
        # 场景一：正常结束（录音中按对话键 → THINKING）
        ctx.press_button(1)
        time.sleep(0.25)
        before = len(calls)
        ctx.press_button(1)
        time.sleep(0.35)
        normal_extra = len(calls) - before
        print(f"  场景一 正常结束   录音调用 {before} 次 → 收尾后 {len(calls)} 次，"
              f"新增 {normal_extra} 次   {'OK' if normal_extra == 0 else '异常'}")

        # 复位到 IDLE，再测取消路径
        ctx.engine.apply(Trigger.AI_FAIL)
        ctx.drain()
        time.sleep(0.2)

        # 场景二：取消录音（录音中按表演键 → IDLE）
        ctx.press_button(1)
        time.sleep(0.25)
        before2 = len(calls)
        ctx.press_button(2)
        time.sleep(0.45)
        cancel_extra = len(calls) - before2
        print(f"  场景二 取消录音   录音调用 {before2} 次 → 取消后 {len(calls)} 次，"
              f"新增 {cancel_extra} 次   {'← 凭空多录一段（F1 复现）' if cancel_extra > 0 else 'OK'}")
        print(f"  最终状态 = {ctx.state_machine.state.value}（期望 IDLE）")
        print(f"  全部录音调用发生在代数 = {calls}")
    finally:
        ctx.close()
    return normal_extra, cancel_extra


# ------------------------------------------------------------------ F2
def f2_rejected_request_has_side_effect() -> None:
    print()
    print(SEP)
    print("F2   被拒绝的 POST /actions/play 是否已经改了状态机")
    print(SEP)
    ctx = _ctx("f2")
    try:
        ctx.press_button(1)          # IDLE -> RECORDING
        ctx.press_button(1)          # REC_STOP -> THINKING
        ctx.engine.apply(Trigger.AI_DONE)   # THINKING -> SPEAKING
        ctx.drain()
        print(f"  调用前状态 = {ctx.state_machine.state.value}（构造为 SPEAKING）")

        raised = None
        try:
            ctx.play_action(role_id=1, action_id="wave")
        except ConflictError as exc:
            raised = str(exc)
        print(f"  play_action 返回      : {'409 ConflictError: ' + raised if raised else '成功（非预期）'}")
        after = ctx.state_machine.state.value
        print(f"  调用后状态            : {after}")
        print(f"  → 拒绝是否带副作用     : {raised is not None and after != 'SPEAKING'}"
              f"   {'← 409 却已把状态改成 ' + after + '（F2 复现）' if raised and after != 'SPEAKING' else 'OK'}")
        print(f"  该次迁移历史          : "
              f"{[(r.source.value, r.trigger.value, r.dest.value) for r in ctx.state_machine.history][-3:]}")
    finally:
        ctx.close()


def main() -> int:
    f1_generations()
    normal_extra, cancel_extra = f1_actual_records()
    f2_rejected_request_has_side_effect()
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  F1 正常结束多录 {normal_extra} 次 / 取消录音多录 {cancel_extra} 次 "
          f"→ {'复现成功（确定缺陷）' if cancel_extra > 0 and normal_extra == 0 else '需人工判读'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
