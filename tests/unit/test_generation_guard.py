"""会话代数守卫的边界：该丢的必须丢，不该丢的一条都不能丢。

``generation`` 守卫的初衷是丢弃「换角色 / 取消本轮之后、上一轮才姗姗来迟的**异步回包**」
（AC-15，见 tests/unit/test_role_switch_generation.py）。但它一旦把**本地命令的回执**
（PLAY_DONE / PERFORM_DONE）也算作「迟到回包」，就会制造**永久驻留**：

``switch_active_role`` 只拒绝 ``PERFORMING`` / ``RECORDING``，因此 SPEAKING 期间换角色是
被允许的（``host/adapters/ports.py``），而换角色会令 ``session_generation + 1``。此时音频
播完发出的 ``PLAY_DONE`` 携带的是旧代数，若被守卫丢弃，状态机就再也收不到「离开 SPEAKING」
的信号 —— 玩具卡在 SPEAKING，直到用户再按一次键。

本文件把这条边界钉死：**只有「跨会话的异步结果」才受代数守卫管辖。**
"""
from __future__ import annotations

from host.core.bus import EventBus, WorkQueue
from host.core.engine import Engine, default_dispatcher
from host.core.events import Event, EventKind
from host.core.state_machine import SessionStateMachine, State


def _engine(state: State, generation: int) -> tuple[Engine, SessionStateMachine]:
    """构造一个只走 ``default_dispatcher`` 的核心（不启线程，便于逐步断言）。"""
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=state, generation=generation)
    return Engine(bus, out, state_machine=machine), machine


# ---------------------------------------------------------------- 不该丢的：本地回执
def test_play_done_survives_generation_bump_during_playback() -> None:
    """播放期间换角色（generation 已 +1）时，PLAY_DONE 仍必须能结束 SPEAKING。"""
    engine, machine = _engine(State.SPEAKING, generation=5)

    default_dispatcher(engine, Event(EventKind.PLAY_DONE, generation=4))

    assert machine.state is State.IDLE, (
        "PLAY_DONE 被代数守卫当作迟到回包丢弃 → SPEAKING 永久驻留（音频已播完却不回 IDLE）"
    )


def test_perform_done_survives_generation_bump() -> None:
    """同理，运动回执 PERFORM_DONE 不得被代数守卫拦住（否则 PERFORMING 永久驻留）。"""
    engine, machine = _engine(State.PERFORMING, generation=7)

    default_dispatcher(engine, Event(EventKind.PERFORM_DONE, generation=6))

    assert machine.state is State.IDLE


# ---------------------------------------------------------------- 该丢的：跨会话异步结果
def test_ai_done_from_previous_session_is_dropped() -> None:
    """上一轮的 AI 回包（旧代数）不得被新一轮会话采纳 —— 否则会把旧回复说出来。"""
    engine, machine = _engine(State.THINKING, generation=5)

    default_dispatcher(engine, Event(EventKind.AI_DONE, generation=4))

    assert machine.state is State.THINKING


def test_ai_fail_from_previous_session_is_dropped() -> None:
    engine, machine = _engine(State.THINKING, generation=5)

    default_dispatcher(engine, Event(EventKind.AI_FAIL, generation=4))

    assert machine.state is State.THINKING


def test_record_stop_from_previous_session_is_dropped() -> None:
    """上一轮录音的迟到收尾不得把**新一轮**录音提前打断成 THINKING。"""
    engine, machine = _engine(State.RECORDING, generation=5)

    default_dispatcher(engine, Event(EventKind.REC_STOP, generation=4))

    assert machine.state is State.RECORDING


# ---------------------------------------------------------------- 同代：正常路径不受影响
def test_current_generation_events_still_advance() -> None:
    """守卫只拦「代数不符」，同代事件必须照常迁移（防止守卫写宽了变成全拦）。"""
    engine, machine = _engine(State.THINKING, generation=5)
    default_dispatcher(engine, Event(EventKind.AI_DONE, generation=5))
    assert machine.state is State.SPEAKING

    default_dispatcher(engine, Event(EventKind.PLAY_DONE, generation=5))
    assert machine.state is State.IDLE
