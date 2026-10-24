"""真实 pyserial/TCP/固件等价模拟器：停止和关停须中断正在发送的动作。"""
from __future__ import annotations

import threading

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.state_machine import State, Trigger
from host.motion.serial_link import SerialLink
from sim.tcp_bridge import TcpBridge, make_controller

from tests.support import TOY_ROOT


class TracedSerialLink(SerialLink):
    def __init__(self, port):
        super().__init__(port, read_timeout_s=0.02)
        self.sessions = []
        self.frame_events = [threading.Event(), threading.Event()]
        self.closed = threading.Event()
        self.open_connections = 0
        self.max_connections = 0

    def open(self):
        super().open()
        self.closed.clear()
        self.sessions.append([])
        self.open_connections += 1
        self.max_connections = max(self.max_connections, self.open_connections)

    def write_line(self, line):
        super().write_line(line)
        self.sessions[-1].append(line)
        if line.startswith("FRAME "):
            self.frame_events[min(len(self.sessions) - 1, 1)].set()

    def close(self):
        was_open = self._ser is not None
        super().close()
        if was_open:
            self.open_connections -= 1
        self.closed.set()


@pytest.fixture()
def rig(tmp_path):
    controllers = []

    def controller_factory(clock):
        controller = make_controller(clock)
        controller.received_bytes = bytearray()
        receive = controller.receive

        def record(data):
            controller.received_bytes.extend(data)
            receive(data)

        controller.receive = record
        controllers.append(controller)
        return controller

    bridge = TcpBridge(port=0, controller_factory=controller_factory).start()
    link = TracedSerialLink(f"socket://127.0.0.1:{bridge.port}")
    link.controllers = controllers
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            link=link, root=TOY_ROOT)
    context.motion.boot_wait_s = 0.3  # 等模拟固件的 200ms 稳定窗口，不改变动作时长。
    context.worker_running = True
    stop = threading.Event()
    worker = threading.Thread(target=context.run_forever, args=(stop,), daemon=True)
    context.register_motion_thread(worker)
    worker.start()
    try:
        yield context, link
    finally:
        stop.set()
        context.close()
        worker.join(6)
        bridge.stop()
        assert not worker.is_alive(), "测试结束后运动线程仍未释放"


@pytest.mark.parametrize("operation", ["stop", "close"])
def test_running_motion_stops_and_closes_transport_promptly(rig, operation):
    context, link = rig
    context.play_action(role_id=1, action_id="wave")  # 原动作长 4 秒。
    assert link.frame_events[0].wait(2), "动作尚未进入实际发帧阶段"
    if operation == "stop":
        context.stop_action()
    else:
        context.close()
    assert link.closed.wait(0.5), "停止请求后仍在发送原动作，未及时 STOP/关闭连接"
    session = link.sessions[0]
    assert session[-1] == "STOP"
    assert sum(line.startswith("FRAME ") for line in session) < 20, "动作仍播放到接近末帧"
    assert link._ser is None
    assert not link.controllers[0].armed
    assert bytes(link.controllers[0].received_bytes).splitlines()[-1] == b"STOP"
    assert context.fault is None, "主动取消不应被当作安全故障锁定"
    assert context.last_result is None, "取消的动作不得被记录成播放成功"
    if operation == "stop":
        assert context.state_machine.state is State.IDLE


def test_replacement_interrupts_old_stream_before_starting_new_connection(rig):
    context, link = rig
    context.play_action(role_id=1, action_id="wave")
    assert link.frame_events[0].wait(2)
    context.play_action(role_id=1, action_id="greet", replace_current=True)
    assert link.frame_events[1].wait(1), "替换动作仍需等待旧动作的 4 秒时间线结束"
    assert len(link.sessions) == 2 and link.sessions[0][-1] == "STOP"
    assert not link.controllers[0].armed
    assert link.max_connections == 1, "新旧动作串口连接发生重叠"
    assert context.state_machine.state is State.PERFORMING
    assert context.fault is None
    context.stop_action()
    assert link.closed.wait(0.5)


def test_safety_lock_cancels_current_stream_without_unlocking(rig):
    context, link = rig
    context.play_action(role_id=1, action_id="wave")
    assert link.frame_events[0].wait(2)
    with context._lock:
        context._deliver(context.engine.apply(Trigger.SAFETY_TRIP))
    assert link.closed.wait(0.5)
    assert context.state_machine.state is State.FAULT_LOCKED
    assert context.last_result is None
