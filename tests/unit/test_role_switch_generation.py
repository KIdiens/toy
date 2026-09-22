"""换角色竞态与会话代数（AC-15）：换角色即 ``session_generation += 1``；迟到回包被丢弃。"""
from __future__ import annotations

import pytest
from host.core.bus import EventBus, WorkQueue
from host.core.engine import Engine, default_dispatcher
from host.core.errors import ConflictError
from host.core.events import Event, EventKind
from host.core.state_machine import SessionStateMachine, State, Trigger
from host.roles.registry import RoleRegistry


def _machine() -> SessionStateMachine:
    return SessionStateMachine()


def test_role_switch_increments_session_generation(toy_root) -> None:
    machine = _machine()
    registry = RoleRegistry(toy_root / "content" / "roles", state_machine=machine)
    registry.load_all()
    registry.set_active(1, bump=False)
    start = machine.generation

    registry.set_active(2)
    assert machine.generation == start + 1, "换角色必须让旧会话回包失效"
    registry.set_active(3)
    assert machine.generation == start + 2


def test_switch_refused_while_performing_keeps_generation(sim_context) -> None:
    generation_before = sim_context.state_machine.generation
    played = sim_context.play_action(role_id=1, action_id="wave")
    assert played["scheduled"] is True
    assert sim_context.state_machine.state is State.PERFORMING

    with pytest.raises(ConflictError, match="不允许切换角色"):
        sim_context.switch_active_role(2)

    assert sim_context.roles.active_role_id == 1, "切换失败不得改动当前角色"
    assert sim_context.state_machine.generation == generation_before, "失败切换不得改代数"


def test_stale_net_result_is_dropped_but_fresh_one_is_processed(monkeypatch) -> None:
    """同一代回包会进入迁移，上一代的回包在派发入口即被丢弃（非空洞断言）。"""
    from host.core import engine as engine_module

    monkeypatch.setitem(engine_module.EVENT_TRIGGERS, EventKind.NET_RESULT, Trigger.AI_DONE)
    machine = _machine()
    machine.reset(initial=State.THINKING)
    engine = Engine(EventBus(), WorkQueue(), state_machine=machine)
    current = machine.generation

    stale = Event(EventKind.NET_RESULT, generation=current - 1)
    assert default_dispatcher(engine, stale) == [], "换角色前的迟到回包必须被丢弃"

    fresh = Event(EventKind.NET_RESULT, generation=current)
    assert default_dispatcher(engine, fresh), "同代会话的回包应正常处理"


def test_cancelled_round_bumps_generation(toy_root) -> None:
    """取消本轮（THINKING 下再按对话键）同样使旧回包失效。"""
    machine = _machine()
    engine = Engine(EventBus(), WorkQueue(), state_machine=machine)
    engine.apply(Trigger.BOOT_OK, {"mcu_ready": True, "role_stable": True})
    engine.apply(Trigger.DIALOG_PRESS)          # IDLE -> RECORDING
    engine.apply(Trigger.REC_STOP)              # RECORDING -> THINKING
    before = machine.generation

    engine.apply(Trigger.DIALOG_PRESS)          # THINKING -> IDLE（取消本轮）
    assert machine.generation == before + 1
