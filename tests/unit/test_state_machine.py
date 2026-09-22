"""状态机穷举：合法迁移、非法迁移被拒（不抛异常）、generation 语义、按键矩阵完整。"""
from __future__ import annotations

import pytest
from host.core.state_machine import (
    BUTTON_DIALOG,
    BUTTON_DISCARD,
    BUTTON_NOOP,
    BUTTON_PERFORM,
    BUTTON_TABLE,
    BUTTON_VOLUME,
    TRANSITIONS,
    WILDCARD,
    SessionStateMachine,
    State,
    Trigger,
    trigger_for_button,
)

ALL_BUTTONS = (BUTTON_DIALOG, BUTTON_PERFORM, BUTTON_VOLUME)


@pytest.mark.parametrize("state", list(State))
@pytest.mark.parametrize("trigger", list(Trigger))
def test_can_fire_matches_table_for_every_pair(state, trigger):
    """逐格比对：can_fire 的判定必须与显式迁移表/通配表完全一致，且绝不抛异常。"""
    machine = SessionStateMachine(initial=state)
    ok, reason = machine.can_fire(trigger, context={})
    if trigger in WILDCARD:
        expected = state is not WILDCARD[trigger]
    else:
        expected = (state, trigger) in TRANSITIONS
    assert ok is expected, f"{state.value}/{trigger.value} 判定与表不一致"
    if not ok:
        assert reason, "被拒时必须给出原因"


@pytest.mark.parametrize("state", list(State))
@pytest.mark.parametrize("trigger", list(Trigger))
def test_fire_never_raises_and_is_noop_when_rejected(state, trigger):
    machine = SessionStateMachine(initial=state)
    before = machine.state
    result = machine.fire(trigger)
    if result.ok:
        assert machine.state is result.dest
    else:
        assert machine.state is before, "被拒时必须保持原状态"
        assert machine.rejections == 1


def test_safety_trip_preempts_every_state_except_locked():
    for state in State:
        machine = SessionStateMachine(initial=state)
        result = machine.fire(Trigger.SAFETY_TRIP)
        if state is State.FAULT_LOCKED:
            assert not result.ok
        else:
            assert result.ok
            assert machine.state is State.FAULT_LOCKED


def test_fault_locked_never_auto_returns(  # AC-02
):
    machine = SessionStateMachine(initial=State.SELF_TEST)
    assert machine.fire(Trigger.BOOT_FAULT).ok
    assert machine.state is State.FAULT_LOCKED
    for trigger in (Trigger.DIALOG_PRESS, Trigger.PERFORM_PRESS, Trigger.IDLE_TICK,
                    Trigger.AI_DONE, Trigger.PLAY_DONE, Trigger.VOLUME_PRESS):
        assert not machine.fire(trigger).ok
        assert machine.state is State.FAULT_LOCKED
    assert machine.fire(Trigger.FAULT_CLEAR).ok
    assert machine.state is State.SELF_TEST


def test_generation_increments_on_entering_idle():
    machine = SessionStateMachine(initial=State.SELF_TEST)
    start = machine.generation
    machine.fire(Trigger.BOOT_OK)
    assert machine.state is State.IDLE
    assert machine.generation == start + 1
    machine.fire(Trigger.DIALOG_PRESS)
    machine.fire(Trigger.REC_CANCEL)
    assert machine.state is State.IDLE
    assert machine.generation == start + 2


def test_generation_increments_when_cancelling_thinking_round():
    machine = SessionStateMachine(initial=State.IDLE)
    before = machine.generation
    machine.fire(Trigger.DIALOG_PRESS)
    machine.fire(Trigger.REC_STOP)
    assert machine.state is State.THINKING
    machine.fire(Trigger.DIALOG_PRESS)  # 取消本轮
    assert machine.state is State.IDLE
    assert machine.generation == before + 1  # 取消本轮并进入 IDLE，+1


def test_switch_role_bumps_generation():
    machine = SessionStateMachine(initial=State.IDLE)
    before = machine.generation
    assert machine.switch_role() == before + 1


def test_volume_press_is_internal_migration():
    for state in (State.IDLE, State.THINKING, State.SPEAKING, State.PERFORMING):
        machine = SessionStateMachine(initial=state)
        before = machine.generation
        result = machine.fire(Trigger.VOLUME_PRESS)
        assert result.ok
        assert machine.state is state, "音量键不得改变状态"
        assert machine.generation == before, "音量键不得改 generation"


def test_button_table_is_total_over_states_and_buttons():
    for state in State:
        for button in ALL_BUTTONS:
            assert (state, button) in BUTTON_TABLE, f"按键矩阵缺格: {state.value} x {button}"


def test_button_matrix_matches_spec_7_1_1():
    assert trigger_for_button(State.IDLE, BUTTON_DIALOG) == Trigger.DIALOG_PRESS.value
    assert trigger_for_button(State.IDLE, BUTTON_PERFORM) == Trigger.PERFORM_PRESS.value
    assert trigger_for_button(State.RECORDING, BUTTON_DIALOG) == Trigger.REC_STOP.value
    assert trigger_for_button(State.RECORDING, BUTTON_PERFORM) == Trigger.REC_CANCEL.value
    assert trigger_for_button(State.RECORDING, BUTTON_VOLUME) == BUTTON_NOOP
    assert trigger_for_button(State.FAULT_LOCKED, BUTTON_DIALOG) == BUTTON_DISCARD
    assert trigger_for_button(State.SELF_TEST, BUTTON_PERFORM) == BUTTON_DISCARD


def test_unknown_state_button_pair_raises():
    with pytest.raises(ValueError):
        trigger_for_button(State.IDLE, 9)


def test_boot_ok_guard_blocks_when_mcu_not_ready():
    machine = SessionStateMachine(initial=State.SELF_TEST)
    ok, reason = machine.can_fire(Trigger.BOOT_OK, context={"mcu_ready": False})
    assert not ok and "MCU" in reason
    assert machine.fire(Trigger.BOOT_OK, context={"mcu_ready": True}).ok


def test_idle_tick_guard():
    machine = SessionStateMachine(initial=State.IDLE)
    ok, reason = machine.can_fire(
        Trigger.IDLE_TICK, context={"role_present": False}
    )
    assert not ok and "公仔" in reason
    assert not machine.can_fire(Trigger.IDLE_TICK, context={"night": True})[0]
    assert machine.can_fire(
        Trigger.IDLE_TICK, context={"idle_seconds": 120, "role_present": True}
    )[0]


def test_full_path_reachable_for_every_declared_transition():
    """每条声明过的迁移都必须能从某状态真实触发（防「写了表但被守卫永久挡住」）。"""
    for (source, trigger), dest in TRANSITIONS.items():
        machine = SessionStateMachine(initial=source)
        context = {"mcu_ready": True, "role_present": True, "idle_seconds": 999}
        result = machine.fire(trigger, context=context)
        assert result.ok, f"{source.value}/{trigger.value} 不可达"
        assert machine.state is dest


def test_no_duplicate_state_transition_keys():
    keys = list(TRANSITIONS.keys())
    assert len(keys) == len(set(keys))
    assert {source for source, _trigger in keys} <= set(State)
    assert {trigger for _source, trigger in keys} <= set(Trigger)
