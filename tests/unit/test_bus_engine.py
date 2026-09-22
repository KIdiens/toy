"""事件总线与确定性核心：单消费者语义、按键驱动迁移、迟到回包丢弃。"""
from __future__ import annotations

import threading

from host.core.bus import EventBus, WorkQueue
from host.core.engine import Engine
from host.core.events import CommandKind, Event, EventKind
from host.core.state_machine import SessionStateMachine, State


def test_work_queue_basic_and_drain():
    queue: WorkQueue[int] = WorkQueue()
    queue.put(1)
    queue.put(2)
    assert queue.qsize() == 2
    assert queue.drain() == [1, 2]
    assert queue.empty()
    assert queue.get(timeout=0.001) is None  # 空队列超时返回 None，不抛


def test_event_bus_shutdown_sentinel():
    bus = EventBus()
    bus.publish(Event(kind=EventKind.BOOT_OK))
    bus.shutdown()
    assert bus.get(timeout=0.1).kind is EventKind.BOOT_OK
    assert bus.get(timeout=0.1).kind is EventKind.SHUTDOWN
    assert bus.closed


def test_engine_drives_state_machine_and_emits_commands():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.IDLE)
    engine = Engine(bus, out, state_machine=machine)
    engine.submit(Event(kind=EventKind.BUTTON, payload={"n": 1}))
    assert engine.step(timeout=0.1) is True
    assert machine.state is State.RECORDING
    kinds = [command.kind for command in out.drain()]
    assert CommandKind.RECORD_START in kinds
    assert CommandKind.NOTIFY in kinds


def test_engine_ignores_discarded_buttons():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.SELF_TEST)
    engine = Engine(bus, out, state_machine=machine)
    engine.submit(Event(kind=EventKind.BUTTON, payload={"n": 2}))
    engine.step(timeout=0.1)
    assert machine.state is State.SELF_TEST
    assert out.empty()


def test_entering_thinking_emits_net_request():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.IDLE)
    engine = Engine(bus, out, state_machine=machine)
    engine.submit(Event(kind=EventKind.BUTTON, payload={"n": 1}))  # IDLE -> RECORDING
    engine.step(timeout=0.1)
    out.drain()
    engine.submit(Event(kind=EventKind.REC_STOP))  # RECORDING -> THINKING
    engine.step(timeout=0.1)
    assert machine.state is State.THINKING
    assert CommandKind.NET_REQUEST in [c.kind for c in out.drain()]


def test_stale_net_result_is_dropped():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.THINKING, generation=7)
    engine = Engine(bus, out, state_machine=machine)
    engine.submit(Event(kind=EventKind.NET_RESULT, generation=6, payload={"ok": True}))
    engine.step(timeout=0.1)
    assert machine.state is State.THINKING, "旧 generation 的迟到回包不得推进状态"
    assert out.empty()


def test_shutdown_stops_engine_loop():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    engine = Engine(bus, out)
    bus.shutdown()
    assert engine.step(timeout=0.1) is False
    assert engine._stopped  # noqa: SLF001  （内部状态断言，验证停机语义）


def test_run_forever_terminates_on_shutdown():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    engine = Engine(bus, out, idle_timeout=0.01)
    thread = threading.Thread(target=engine.run_forever, daemon=True)
    thread.start()
    bus.shutdown()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_mcu_fault_trips_safety_lock():
    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.PERFORMING)
    engine = Engine(bus, out, state_machine=machine)
    engine.submit(Event(kind=EventKind.MCU_FAULT, payload={"line": "FAULT INTERLOCK_OR_TIMEOUT"}))
    engine.step(timeout=0.1)
    assert machine.state is State.FAULT_LOCKED
