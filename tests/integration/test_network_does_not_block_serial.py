"""AC-22 非阻塞证明：网络线程注入 10 秒假请求时，串口与按键仍在 <100ms 内响应。

两个层次：

- 直接证明：一个线程阻塞 10 秒，串口 STATUS 往返与核心按键处理都在 <100ms 内完成。
- 结构证明：核心把 ASR/LLM 交给 **net 线程**（``NET_REQUEST`` 命令），核心自身不阻塞；
  在 net 线程被 10 秒假请求占住期间，核心仍即时处理按键。
"""
from __future__ import annotations

import threading
import time

import pytest
from host.core.bus import EventBus, WorkQueue
from host.core.clock import RealClock
from host.core.engine import Engine
from host.core.events import CommandKind, Event, EventKind
from host.core.state_machine import SessionStateMachine, State
from host.motion.sim_link import SimLink
from sim.controller import McController

FAKE_NETWORK_SECONDS = 10.0
DEADLINE_MS = 100.0


def _now() -> float:
    return time.perf_counter() * 1000.0


def _status_round_trip(link: SimLink, deadline_ms: float = DEADLINE_MS) -> tuple[bool, float]:
    started = _now()
    link.write_line("STATUS")
    until = started + deadline_ms
    while _now() < until:
        for line in link.read_lines():
            if line.startswith("STATE "):
                return True, _now() - started
        time.sleep(0.0005)
    return False, _now() - started


def _await_state(machine: SessionStateMachine, target: State, timeout_ms: float = 500.0) -> None:
    deadline = _now() + timeout_ms
    while machine.state is not target and _now() < deadline:
        time.sleep(0.0005)
    assert machine.state is target, f"未在 {timeout_ms}ms 内进入 {target.value}"


def test_network_does_not_block_serial_and_buttons():
    clock = RealClock()
    controller = McController(clock)
    link = SimLink(controller, clock=clock)
    link.open()

    busy = threading.Event()
    done = threading.Event()

    def fake_network_request() -> None:
        busy.set()
        time.sleep(FAKE_NETWORK_SECONDS)  # 10 秒假请求，释放 GIL
        done.set()

    net = threading.Thread(target=fake_network_request, name="fake-net", daemon=True)
    net.start()
    assert busy.wait(1.0), "假网络请求未启动"

    ok, latency = _status_round_trip(link)
    assert ok, "假网络请求期间串口无响应"
    assert latency < DEADLINE_MS, f"串口往返 {latency:.1f}ms 超过 {DEADLINE_MS}ms"

    bus = EventBus()
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=State.IDLE)
    engine = Engine(bus, out, state_machine=machine, clock=clock)
    core = threading.Thread(target=engine.run_forever, name="core", daemon=True)
    core.start()

    started = _now()
    bus.publish(Event(kind=EventKind.BUTTON, payload={"n": 1}))
    while machine.state is not State.RECORDING and _now() - started < DEADLINE_MS:
        time.sleep(0.0005)
    button_latency = _now() - started
    assert machine.state is State.RECORDING, "假网络请求期间按键未生效"
    assert button_latency < DEADLINE_MS, f"按键处理 {button_latency:.1f}ms 超过 {DEADLINE_MS}ms"

    assert not done.is_set(), "假请求应当仍在进行（证明这确实是长阻塞）"
    engine.stop()
    core.join(timeout=1)
    link.close()


class _RoutingQueue:
    """把核心命令按种类路由到不同工作线程队列（net 与 motion 物理隔离）。"""

    def __init__(self, motion_q: WorkQueue, net_q: WorkQueue) -> None:
        self.motion_q = motion_q
        self.net_q = net_q

    def put(self, command) -> None:
        if command.kind is CommandKind.NET_REQUEST:
            self.net_q.put(command)
        else:
            self.motion_q.put(command)


def test_core_stays_responsive_while_net_worker_is_blocked():
    clock = RealClock()
    controller = McController(clock)
    link = SimLink(controller, clock=clock)
    link.open()

    bus = EventBus()
    motion_q: WorkQueue = WorkQueue(name="motion")
    net_q: WorkQueue = WorkQueue(name="net")
    machine = SessionStateMachine(initial=State.IDLE)
    engine = Engine(bus, _RoutingQueue(motion_q, net_q), state_machine=machine, clock=clock)

    net_busy = threading.Event()
    net_done = threading.Event()
    stop_workers = threading.Event()

    def net_worker() -> None:
        while not stop_workers.is_set():
            command = net_q.get(timeout=0.05)
            if command is not None and command.kind is CommandKind.NET_REQUEST:
                net_busy.set()
                time.sleep(FAKE_NETWORK_SECONDS)  # 假 ASR/LLM 往返
                net_done.set()

    def motion_worker() -> None:
        while not stop_workers.is_set():
            command = motion_q.get(timeout=0.02)
            if command is not None and command.kind in (
                CommandKind.STOP,
                CommandKind.STOP_MOTION,
            ):
                link.write_line("STOP")
            link.read_lines()

    net_thread = threading.Thread(target=net_worker, name="net", daemon=True)
    motion_thread = threading.Thread(target=motion_worker, name="motion", daemon=True)
    core = threading.Thread(target=engine.run_forever, name="core", daemon=True)
    for thread in (net_thread, motion_thread, core):
        thread.start()

    # 触发一轮对话：IDLE -> RECORDING -> THINKING，进入 THINKING 即下发 NET_REQUEST
    bus.publish(Event(kind=EventKind.BUTTON, payload={"n": 1}))
    _await_state(machine, State.RECORDING)
    bus.publish(Event(kind=EventKind.REC_STOP))
    _await_state(machine, State.THINKING)
    assert net_busy.wait(1.0), "NET_REQUEST 未到达 net 线程"

    ok, latency = _status_round_trip(link)
    assert ok and latency < DEADLINE_MS, f"核心/串口被网络阻塞（{latency:.1f}ms）"

    started = _now()
    bus.publish(Event(kind=EventKind.BUTTON, payload={"n": 1}))  # THINKING -> IDLE
    while machine.state is not State.IDLE and _now() - started < DEADLINE_MS:
        time.sleep(0.0005)
    assert machine.state is State.IDLE, "net 线程被占住时核心无法处理按键"
    assert _now() - started < DEADLINE_MS
    assert not net_done.is_set(), "net 线程应仍被假请求占住"

    stop_workers.set()
    engine.stop()
    core.join(timeout=1)
    net_thread.join(timeout=0.2)
    motion_thread.join(timeout=0.2)
    link.close()


@pytest.mark.parametrize("rounds", [5])
def test_repeated_serial_round_trips_stay_fast_under_blocking_network(rounds):
    clock = RealClock()
    controller = McController(clock)
    link = SimLink(controller, clock=clock)
    link.open()
    thread = threading.Thread(target=time.sleep, args=(FAKE_NETWORK_SECONDS,), daemon=True)
    thread.start()

    latencies = [_status_round_trip(link)[1] for _ in range(rounds)]
    link.close()
    assert max(latencies) < DEADLINE_MS, f"最大往返 {max(latencies):.1f}ms"
