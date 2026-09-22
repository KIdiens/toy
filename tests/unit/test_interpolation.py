"""插值与时间轴：端点、中位、全程限位、smoothstep、按经过时间采样。"""
from __future__ import annotations

from host.core.clock import VirtualClock
from host.motion.interpolation import sample, smoothstep
from host.motion.timeline import Timeline


def test_smoothstep_anchors():
    assert smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0
    assert smoothstep(0.5) == 0.5


def test_sample_endpoints(wave_action):
    assert sample(wave_action, -10) == [1500] * 4
    assert sample(wave_action, 0) == [1500] * 4
    assert sample(wave_action, 99999) == [1500] * 4


def test_sample_midpoint(wave_action):
    assert sample(wave_action, 500) == [1500, 1550, 1510, 1500]


def test_every_sample_stays_within_hard_limits(wave_action):  # AC-11
    for t_ms in range(0, 4001):
        values = sample(wave_action, t_ms)
        assert all(1400 <= v <= 1600 for v in values)


def test_timeline_uses_elapsed_time_not_accumulated_sleep(wave_action):
    clock = VirtualClock()
    timeline = Timeline(wave_action, clock)
    timeline.begin()
    assert timeline.elapsed_ms() == 0
    assert timeline.frame() == [1500] * 4

    clock.advance(2000)
    assert timeline.elapsed_ms() == 2000
    assert not timeline.at_end()

    clock.advance(2500)  # 越过末帧
    assert timeline.clamped_elapsed_ms() == 4000
    assert timeline.overshoot_ms() == 500
    assert timeline.at_end()
    assert timeline.frame() == [1500] * 4


def test_timeline_frame_matches_sample(wave_action):
    clock = VirtualClock()
    timeline = Timeline(wave_action, clock)
    timeline.begin()
    for delta in (0, 250, 1000, 1999, 4000):
        clock.advance(delta)
        assert timeline.frame() == sample(wave_action, timeline.clamped_elapsed_ms())
