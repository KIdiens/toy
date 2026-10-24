"""动作各等待阶段均可取消；正常路径仍保持原有线序与末帧保持。"""
from __future__ import annotations

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.clock import VirtualClock
from host.core.errors import MotionCancelled
from host.core.events import EventKind
from host.core.state_machine import State
from host.motion.client import MotionClient, PlayResult
from host.motion.sim_link import SimLink
from sim.tcp_bridge import make_controller

from tests.support import TOY_ROOT

ACTION = {"schema": 1, "role_id": 1, "name": "hold", "frames": [
    {"t_ms": 0, "us": [1500] * 4}, {"t_ms": 200, "us": [1500] * 4},
]}


class TraceLink(SimLink):
    def __init__(self, clock, phase="normal"):
        super().__init__(make_controller(clock), clock=clock)
        self.phase = phase
        self.opened = self.closed = False
        self.writes = []
        self.settled = False
        self.first_frame_at = self.final_seq = self.settled_at = None

    def open(self):
        self.opened = True
        super().open()

    def close(self):
        self.closed = True
        super().close()

    def write_line(self, line):
        self.writes.append((self.clock.now_ms(), line))
        if line.startswith("FRAME "):
            if self.first_frame_at is None:
                self.first_frame_at = self.clock.now_ms()
            if self.clock.now_ms() - self.first_frame_at >= 200 and self.final_seq is None:
                self.final_seq = int(line.split()[1])
        super().write_line(line)

    def read_lines(self):
        lines = super().read_lines()
        if self.final_seq is not None and any(
            line.startswith("SETTLED ") and int(line.split()[1]) >= self.final_seq for line in lines
        ):
            self.settled = True
            self.settled_at = self.clock.now_ms()
        if self.phase == "status":
            return []
        if self.phase == "arm":
            return [line for line in lines if not line.startswith("OK ARM ")]
        return lines


@pytest.mark.parametrize("phase", ["before-open", "boot", "status", "arm", "stream", "hold"])
def test_cancellation_stops_without_finishing_or_rearming(phase):
    clock = VirtualClock()
    link = TraceLink(clock, phase)
    client = MotionClient(link, clock)

    def cancelled():
        frames = sum(line.startswith("FRAME ") for _, line in link.writes)
        return (phase == "before-open" or phase == "boot" and clock.now_ms() >= 80
                or phase in {"status", "arm"} and clock.now_ms() >= 2080
                or phase == "stream" and frames >= 3
                or phase == "hold" and link.settled)

    with pytest.raises(MotionCancelled):
        client.play(ACTION, cancel=cancelled)
    commands = [line for _, line in link.writes]
    if phase == "before-open":
        assert not link.opened and not link.closed and not commands
        return
    assert link.closed and commands[-1] == "STOP"
    assert sum(line == "ARM 1" for line in commands) <= 1, "取消后不能自动重新解锁"
    if phase in {"boot", "status"}:
        assert not any(line.startswith("ARM ") for line in commands)
    if phase in {"boot", "status", "arm"}:
        assert not any(line.startswith("FRAME ") for line in commands)
    if phase == "stream":
        assert sum(line.startswith("FRAME ") for line in commands) == 3
    if phase == "hold":
        assert link.settled
        assert clock.now_ms() - link.settled_at < 150, "取消不应继续等待正常末帧保持窗口"


def test_non_cancelled_callback_preserves_complete_wire_timing_and_result():
    runs = []
    for cancel in (None, lambda: False):
        clock = VirtualClock()
        link = TraceLink(clock)
        result = MotionClient(link, clock).play(ACTION, cancel=cancel)
        assert result.hold_ms >= 150 and link.closed
        runs.append((result, link.writes))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("replace", [False, True],
                         ids=["stop-before-return", "replace-before-return"])
def test_cancel_after_last_io_still_discards_completion(tmp_path, replace):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            simulate=True, root=TOY_ROOT)
    events = []
    entered = []
    context.drain()  # 先消费上电通知，下一次 pump 才确实是目标动作。
    original_publish = context.bus.publish

    def publish(event):
        events.append(event.kind)
        return original_publish(event)

    class CompletedMotion:
        def play(self, action, **kwargs):
            entered.append(action)
            # 最后一次 I/O 已结束，取消/替换恰好发生在结果交还适配层之前。
            if replace:
                context.play_action(role_id=1, action_id="greet", replace_current=True)
            else:
                context.stop_action()
            return PlayResult(1, 10, 9, 150, 4000)

    context.bus.publish = publish
    context.motion = CompletedMotion()
    context.worker_running = True  # 由本用例显式 pump，不能让 stop_action 清掉待验证的队列。
    try:
        context.play_action(role_id=1, action_id="wave")
        context.pump_once()
        assert len(entered) == 1, "必须真正走到播放结果返回边界，不能只消费上电通知"
        assert context.last_result is None
        assert EventKind.PERFORM_DONE not in events
        assert context.state_machine.state is (State.PERFORMING if replace else State.IDLE)
        assert context.motion_busy is replace
    finally:
        context.close()
