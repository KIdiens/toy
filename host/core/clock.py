"""时钟抽象：生产用 ``RealClock``，测试用 ``VirtualClock``（零真实等待、可复现）。

模拟器与 ``MotionClient`` 都只依赖注入的 ``Clock``。``VirtualClock`` 额外提供
``advance(ms)`` 与监听器，让进程内模拟器的时基与主机虚拟时钟严格同步。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

DEFAULT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def format_iso(moment: datetime) -> str:
    """统一的 ISO-8601 UTC 文本（``YYYY-MM-DDTHH:MM:SSZ``），存储层唯一时间格式。"""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


@runtime_checkable
class Clock(Protocol):
    """最小时钟契约。"""

    def now_ms(self) -> int: ...

    def sleep(self, seconds: float) -> None: ...

    def now_iso(self) -> str: ...


class RealClock:
    """真实单调时钟（毫秒）+ UTC 墙上时间。"""

    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now_iso(self) -> str:
        return format_iso(datetime.now(UTC))


class VirtualClock:
    """虚拟时钟：``sleep`` 只前进不等待；``advance`` 手动推进；可挂监听器。

    ``sleep`` 前进会让已注册的监听器（进程内模拟器）同步推进时基，使主机虚拟
    时间与 MCU 的 ``millis()`` 严格一致，无需真实等待即可复现看门狗/报告节奏。
    """

    def __init__(self, start_ms: int = 0, *, start_iso: str | None = None) -> None:
        self._now = int(start_ms)
        self._base = parse_iso(start_iso) if start_iso else DEFAULT_EPOCH
        self._listeners: list[Callable[[int], None]] = []

    def now_ms(self) -> int:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(int(round(seconds * 1000)))

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("虚拟时钟只能向前推进")
        self._now += int(ms)
        for listener in tuple(self._listeners):
            listener(self._now)
        return self._now

    def add_listener(self, listener: Callable[[int], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[int], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def now_iso(self) -> str:
        return format_iso(self._base + timedelta(milliseconds=self._now))
