"""换角色必须**立即**收敛，不得把设备留在 THINKING 等 60s兜底（第6 组D7）。

**为什么这组测试的形状这么定**：此前`test_role_switch_generation.py:49` 用
``machine.reset(initial=State.THINKING)`` 直接造状态再测**取消**路径，于是
「THINKING 中换角色会不会立即收敛」从来没被断言过 —— 那正是本缺陷漏网的原因。
故这里全部走**真实路径**（按对话键 → 进 THINKING → 换角色），并断言「立即」离开
THINKING，而不是「最终会离开」：后者在 ``NET_THINKING_TIMEOUT_S=60.0`` 的 tick
兜底下同样成立，是恒绿的假测试。
"""
from __future__ import annotations

import pytest
from host.core.errors import ConflictError
from host.core.state_machine import State

BUTTON_DIALOG = 1
BUTTON_PERFORM = 2


def _enter_thinking_by_real_path(ctx) -> None:
    """走真实按键路径进 THINKING：IDLE -> RECORDING -> THINKING。

    **不用** ``machine.reset(initial=THINKING)`` 造假状态 —— 那正是原缺陷漏检的原因。
    """
    ctx.press_button(BUTTON_DIALOG)
    assert ctx.state_machine.state is State.RECORDING, "第一次按对话键应进 RECORDING"
    ctx.press_button(BUTTON_DIALOG)
    assert ctx.state_machine.state is State.THINKING, "第二次按对话键应进 THINKING"


# ------------------------------------------------------------------ 缺陷本体
def test_switch_role_in_thinking_leaves_thinking_immediately(sim_context) -> None:
    """THINKING 中换角色：请求成功，且**当场**离开 THINKING。

    「立即」的可观测判据：函数返回的那一刻状态机已不在 THINKING。
    不依赖任何超时兜底、不sleep、不跑 tick —— 若把修复回退，本条立刻转红。
    """
    _enter_thinking_by_real_path(sim_context)
    assert sim_context.state_machine.state is State.THINKING

    result = sim_context.switch_active_role(2)

    assert result["role_id"] == 2, "换角色本身必须成功（不得改成拒绝）"
    assert sim_context.state_machine.state is State.IDLE, (
        "换角色后必须立即离开 THINKING；留在 THINKING 意味着要等"
        "NET_THINKING_TIMEOUT_S=60.0 的 tick 兜底才收敛"
    )
    assert sim_context.roles.active_role_id == 2


def test_switch_role_in_thinking_makes_stale_reply_droppable(
    sim_context, monkeypatch
) -> None:
    """换角色后，**被取消那一代**的 AI 回包必须仍被丢弃（代数语义未被破坏）。"""
    from host.core import engine as engine_module
    from host.core.engine import default_dispatcher
    from host.core.events import Event, EventKind
    from host.core.state_machine import Trigger

    _enter_thinking_by_real_path(sim_context)
    stale_generation = sim_context.state_machine.generation
    sim_context.switch_active_role(2)

    assert sim_context.state_machine.generation > stale_generation, (
        "换角色必须推进代数，否则旧回包仍会被当成本轮回包说出来"
    )

    monkeypatch.setitem(engine_module.EVENT_TRIGGERS, EventKind.NET_RESULT, Trigger.AI_DONE)
    stale = Event(EventKind.NET_RESULT, generation=stale_generation)
    assert default_dispatcher(sim_context.engine, stale) == [], (
        "被取消那一代的回包必须丢弃"
    )


def test_abandoned_round_does_not_trigger_inference(sim_context) -> None:
    """被取消那一轮遗留的 NET_REQUEST 不得白跑一次 ASR/LLM（代数不符即丢弃）。"""
    _enter_thinking_by_real_path(sim_context)
    command = sim_context.net_queue.get(timeout=0)
    assert command is not None and command.kind.value == "net_request"

    sim_context.switch_active_role(2)

    assert command.generation != sim_context.state_machine.generation, (
        "遗留 NET_REQUEST 的代数必须落后于当前代数，否则 net worker 会真的发起推理"
    )


# ------------------------------------------------------------------ 代数 +2 的语义等价性
def test_generation_may_advance_twice_and_stays_correct(sim_context) -> None:
    """取消本轮(+1) + set_active(+1) = +2；代数只用于相等比较，故仍正确。

    这条钉住「+2 不算缺陷」：若将来有人给代数引入相对运算（如 ``gen - 1`` 判陈旧），
    本条会在其破坏语义前先提醒——至少它让+2 变成一个**被明确承认**的事实。
    """
    _enter_thinking_by_real_path(sim_context)
    before = sim_context.state_machine.generation

    sim_context.switch_active_role(2)
    after = sim_context.state_machine.generation

    assert after == before + 2, (
        f"实测代数应 +2（取消本轮 +1、set_active +1），实际 {after - before}"
    )
    assert after > before, "无论如何代数必须严格前进，旧回包必须失效"


# ------------------------------------------------------------------ 防修过头（对照组）
@pytest.mark.parametrize("setup", ["recording", "performing"])
def test_switch_role_still_refused_while_recording_or_performing(
    sim_context, setup: str
) -> None:
    """**防修过头**：RECORDING / PERFORMING 中换角色仍然必须 409，且零副作用。"""
    if setup == "recording":
        sim_context.press_button(BUTTON_DIALOG)
        assert sim_context.state_machine.state is State.RECORDING
    else:
        sim_context.play_action(role_id=1, action_id="wave")
        assert sim_context.state_machine.state is State.PERFORMING

    generation_before = sim_context.state_machine.generation
    state_before = sim_context.state_machine.state

    with pytest.raises(ConflictError, match="不允许切换角色"):
        sim_context.switch_active_role(2)

    assert sim_context.roles.active_role_id == 1, "被拒的切换不得改动当前角色"
    assert sim_context.state_machine.generation == generation_before, (
        "被拒的切换不得改代数"
    )
    assert sim_context.state_machine.state is state_before, "被拒的切换不得改状态"


def test_switch_role_in_idle_is_unchanged(sim_context) -> None:
    """IDLE 中换角色：不受本次修复影响（不额外+1，不做多余迁移）。"""
    assert sim_context.state_machine.state is State.IDLE
    before = sim_context.state_machine.generation

    result = sim_context.switch_active_role(3)

    assert result["role_id"] == 3
    assert sim_context.state_machine.state is State.IDLE
    assert sim_context.state_machine.generation == before + 1, (
        "IDLE 中换角色应只 +1（只有 set_active 一次递增）"
    )


def test_switch_role_while_speaking_still_relies_on_play_done(sim_context) -> None:
    """SPEAKING 中换角色**不受本次修复影响**：不被取消，靠 PLAY_DONE 自行收敛。

    钉住「为什么SPEAKING 一律不动」：``(SPEAKING, DIALOG_PRESS)`` 指向 RECORDING，
    拿它当「取消」会开始录音；而 PLAY_DONE 不在 STALE_SENSITIVE 内且被无条件回投，
    所以 SPEAKING 本就有确定性出口，不存在 60s 静默。
    """
    from host.core.engine import STALE_SENSITIVE
    from host.core.events import Event, EventKind

    assert EventKind.PLAY_DONE not in STALE_SENSITIVE, (
        "PLAY_DONE 若被纳入陈旧敏感集，换角色期间的 PLAY_DONE 会被丢弃，"
        "SPEAKING 将永久驻留（engine.py 的既有反例）"
    )

    sim_context.state_machine.reset(initial=State.SPEAKING)
    generation_before = sim_context.state_machine.generation
    sim_context.switch_active_role(3)

    assert sim_context.state_machine.state is State.SPEAKING, (
        "SPEAKING 不该被本次修复取消（其出边会指向 RECORDING）"
    )
    # 旧一代的 PLAY_DONE 仍应驱动 SPEAKING -> IDLE
    from host.core.engine import default_dispatcher

    default_dispatcher(
        sim_context.engine,
        Event(EventKind.PLAY_DONE, generation=generation_before),
    )
    assert sim_context.state_machine.state is State.IDLE, (
        "SPEAKING 必须靠 PLAY_DONE 确定性收敛（_play_clip 无条件回投它）"
    )