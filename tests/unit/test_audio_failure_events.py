"""音频失败是独立迁移；旧轮次与不匹配阶段不得影响当前操作。"""
from __future__ import annotations

import pytest
from host.audio.backend import Recording
from host.core.bus import EventBus, WorkQueue
from host.core.engine import Engine, default_dispatcher
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import SessionStateMachine, State, Trigger


@pytest.mark.parametrize("state,phase", [
    (State.RECORDING, "record"), (State.THINKING, "record"), (State.SPEAKING, "play"),
])
def test_audio_failure_ends_the_round_as_failure(state, phase):
    machine = SessionStateMachine(initial=state, generation=6)
    engine = Engine(EventBus(), WorkQueue(), state_machine=machine)
    default_dispatcher(engine, Event(EventKind.AUDIO_FAIL, {"phase": phase}, generation=6))
    assert machine.state is State.IDLE and machine.generation == 7
    assert machine.history[-1].trigger is Trigger.AUDIO_FAIL


@pytest.mark.parametrize("state,phase", [
    (State.RECORDING, "record"), (State.THINKING, "record"), (State.SPEAKING, "play"),
])
def test_old_generation_audio_failure_is_discarded(state, phase):
    machine = SessionStateMachine(initial=state, generation=6)
    engine = Engine(EventBus(), WorkQueue(), state_machine=machine)
    default_dispatcher(engine, Event(EventKind.AUDIO_FAIL, {"phase": phase}, generation=5))
    assert machine.state is state and machine.generation == 6


@pytest.mark.parametrize("state,phase", [
    (State.RECORDING, "play"), (State.SPEAKING, "record"), (State.THINKING, "play"),
    (State.PERFORMING, "record"), (State.FAULT_LOCKED, "play"), (State.IDLE, "record"),
    (State.RECORDING, "unknown"),
])
def test_audio_failure_does_not_cross_phase_or_unlock_safety_fault(state, phase):
    machine = SessionStateMachine(initial=state, generation=6)
    engine = Engine(EventBus(), WorkQueue(), state_machine=machine)
    default_dispatcher(engine, Event(EventKind.AUDIO_FAIL, {"phase": phase}, generation=6))
    assert machine.state is state and machine.generation == 6


@pytest.mark.parametrize("recording", [None, Recording(b"", 16000)])
def test_cancelled_or_empty_capture_never_calls_speech_service(sim_context, monkeypatch, recording):
    monkeypatch.setattr(sim_context, "_await_recording", lambda generation: recording)
    monkeypatch.setattr(sim_context.router, "respond_audio",
                        lambda *a, **k: pytest.fail("没有录音仍调用了语音服务"))
    faults = sim_context.worker_faults
    sim_context._handle_net_request(Command(CommandKind.NET_REQUEST,
                                            generation=sim_context.state_machine.generation))
    assert sim_context.worker_faults == faults, "用户取消不应被记录为线程故障"


@pytest.mark.parametrize("kind", [CommandKind.RECORD_START, CommandKind.PLAY_AUDIO])
def test_replaced_operation_failure_cannot_finish_the_new_operation(sim_context, kind):
    generation = sim_context.state_machine.generation
    old = sim_context.audio_control.prepare(Command(kind, generation=generation))
    sim_context.audio_control.prepare(Command(kind, generation=generation))
    before = sim_context.bus.qsize()
    sim_context._fail_audio(old, RuntimeError("old stream failed"))
    assert sim_context.bus.qsize() == before


def test_interrupted_playback_failure_does_not_publish_another_finish_event(sim_context):
    generation = sim_context.state_machine.generation
    old = sim_context.audio_control.prepare(Command(CommandKind.PLAY_AUDIO, generation=generation))
    sim_context.audio_control.prepare(Command(CommandKind.STOP_AUDIO, generation=generation))
    before = sim_context.bus.qsize()
    sim_context._fail_audio(old, RuntimeError("interrupted stream failed"))
    assert sim_context.bus.qsize() == before
