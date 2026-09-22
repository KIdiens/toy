"""时钟：虚拟时钟推进/睡眠/监听器/ISO 文本；真实时钟单调与格式。"""
from __future__ import annotations

import re

import pytest
from host.core.clock import RealClock, VirtualClock, parse_iso

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_virtual_clock_advance_and_sleep():
    clock = VirtualClock(start_ms=100)
    assert clock.now_ms() == 100
    clock.advance(50)
    assert clock.now_ms() == 150
    clock.sleep(0.02)  # 不真实等待，只前进 20ms
    assert clock.now_ms() == 170
    clock.sleep(2.0)
    assert clock.now_ms() == 2170


def test_virtual_clock_rejects_backwards():
    clock = VirtualClock()
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_virtual_clock_listeners_fire_on_advance_and_sleep():
    clock = VirtualClock()
    seen: list[int] = []
    clock.add_listener(seen.append)
    clock.add_listener(seen.append)  # 重复注册去重
    clock.advance(10)
    clock.sleep(0.005)
    assert seen == [10, 15]
    clock.remove_listener(seen.append)
    clock.advance(5)
    assert seen == [10, 15]  # 已移除，不再触发
    clock.remove_listener(seen.append)  # 重复移除不报错


def test_virtual_clock_iso_is_deterministic():
    clock = VirtualClock(start_ms=0, start_iso="2026-01-01T00:00:00Z")
    assert clock.now_iso() == "2026-01-01T00:00:00Z"
    clock.advance(3661_000)
    assert clock.now_iso() == "2026-01-01T01:01:01Z"


def test_real_clock_is_monotonic_and_iso_formatted():
    clock = RealClock()
    first = clock.now_ms()
    clock.sleep(0.001)
    second = clock.now_ms()
    assert second >= first
    assert ISO_RE.match(clock.now_iso())


def test_parse_iso_normalizes_to_utc():
    moment = parse_iso("2026-01-01T08:00:00+08:00")
    assert moment.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-01-01T00:00:00Z"
