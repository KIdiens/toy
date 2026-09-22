"""``MotionClient`` + ``SimLink``（DI 版）全链路：顺序、阈值、异常路径与安全停机。"""
from __future__ import annotations

import pytest
from host.core.clock import VirtualClock
from host.core.errors import InterlockError, MotionTimeout, ProtocolError
from host.motion.client import MotionClient
from host.motion.sim_link import SimLink
from sim.controller import McController
from sim.devices import Estop, RoleResistor


class SpyLink:
    """记录主机写入、可注入断流/隐藏 SETTLED/中途改角色，用于异常路径。"""

    def __init__(self, inner, *, on_frame=None, drop_frames_after=None, hide_settled=False):
        self.inner = inner
        self.writes: list[str] = []
        self.frames = 0
        self._on_frame = on_frame
        self._drop_after = drop_frames_after
        self._hide = hide_settled

    def open(self) -> None:
        self.inner.open()

    def close(self) -> None:
        self.inner.close()

    def reset_input_buffer(self) -> None:
        self.inner.reset_input_buffer()

    def write_line(self, line: str) -> None:
        self.writes.append(line)
        if line.startswith("FRAME "):
            self.frames += 1
            if self._on_frame is not None:
                self._on_frame(self.frames)
            if self._drop_after is not None and self.frames >= self._drop_after:
                return  # 断流：模拟主机停发帧 / 线缆中断
        self.inner.write_line(line)

    def read_lines(self) -> list[str]:
        lines = self.inner.read_lines()
        if self._hide:
            lines = [line for line in lines if not line.startswith("SETTLED")]
        return lines


def _build(role: int = 1, *, estop: bool = False, link_kwargs: dict | None = None):
    clock = VirtualClock()
    link_kwargs = link_kwargs or {}
    controller = McController(
        clock, role=RoleResistor(role=role), estop=Estop(pressed=estop)
    )
    spy = SpyLink(SimLink(controller, clock=clock), **link_kwargs)
    client = MotionClient(spy, clock)
    return controller, spy, client, clock


def test_happy_path_order_and_thresholds(wave_action):
    controller, spy, client, _clock = _build()
    result = client.play(wave_action)

    assert "ARM 1" in spy.writes
    assert spy.writes.count("ARM 1") == 1, "绝不重复解锁"
    assert spy.writes[0] == "STOP"  # 前置 STOP
    assert spy.writes[-1] == "STOP"  # finally 必定 STOP
    assert result.hold_ms >= 150, "末帧必须保持 >=150ms 再 STOP"  # AC-08
    assert result.frames_sent > 2
    assert controller.armed is False, "结束后必须已停机"


def test_each_round_starts_with_stop_and_single_arm(wave_action):
    _controller, spy, client, _clock = _build()
    client.play(wave_action)
    spy.writes.clear()
    client.play(wave_action)
    assert spy.writes[0] == "STOP", "每轮播放必须先 STOP 再 STATUS 再 ARM（Spec §11 已知坑）"
    assert spy.writes.count("ARM 1") == 1


def test_wrong_role_never_arms(wave_action):  # AC-05
    _controller, spy, client, _clock = _build(role=2)
    with pytest.raises(InterlockError, match="角色编号"):
        client.play(wave_action)
    assert not any(line.startswith("ARM") for line in spy.writes)
    assert spy.writes[-1] == "STOP"


def test_estop_during_motion_aborts_and_stops(wave_action):  # AC-03
    def trip(frame_count: int) -> None:
        if frame_count == 5:
            estop.pressed = True

    estop = Estop()
    clock = VirtualClock()
    controller = McController(clock, estop=estop)
    spy = SpyLink(SimLink(controller, clock=clock), on_frame=trip)
    client = MotionClient(spy, clock)
    with pytest.raises(ProtocolError, match="FAULT"):
        client.play(wave_action)
    assert spy.writes[-1] == "STOP"


def test_watchdog_stops_when_frames_stop_flowing(wave_action):  # AC-06
    _controller, spy, client, _clock = _build(link_kwargs={"drop_frames_after": 3})
    with pytest.raises(ProtocolError, match="INTERLOCK_OR_TIMEOUT"):
        client.play(wave_action)
    assert spy.writes[-1] == "STOP"


def test_role_swap_during_motion_aborts(wave_action):  # AC-15 主机侧
    def swap(frame_count: int) -> None:
        if frame_count == 5:
            controller.role_res.role = 2

    clock = VirtualClock()
    controller = McController(clock)
    spy = SpyLink(SimLink(controller, clock=clock), on_frame=swap)
    client = MotionClient(spy, clock)
    with pytest.raises((InterlockError, ProtocolError)):
        client.play(wave_action)
    assert spy.writes[-1] == "STOP"


def test_missing_settled_times_out_and_stops(wave_action):  # AC-07
    _controller, spy, client, _clock = _build(link_kwargs={"hide_settled": True})
    with pytest.raises(MotionTimeout, match="末帧"):
        client.play(wave_action)
    assert spy.writes[-1] == "STOP"


def test_never_re_arms_automatically(wave_action):
    _controller, spy, client, _clock = _build(link_kwargs={"hide_settled": True})
    with pytest.raises(MotionTimeout):
        client.play(wave_action)
    assert spy.writes.count("ARM 1") == 1, "超时后绝不自动重新解锁"


def test_boot_wait_advances_virtual_clock(wave_action):
    _controller, _spy, client, clock = _build()
    client.play(wave_action)
    assert clock.now_ms() > 4000, "虚拟时钟应已推进过整段动作时长"
