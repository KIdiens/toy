"""模拟器自带的极小时钟（不 import host.*，保持依赖只向下）。

- ``SimClock``：虚拟时钟，``advance`` 手动推进，``sleep`` 只前进不真实等待。
- ``WallClock``：真实单调时钟，供 ``--tcp`` 长驻模式驱动主循环。

生产的 ``host.core.clock.VirtualClock`` 具备同样的 ``now_ms()`` 鸭子接口，
测试可直接把它注入 ``McController``，无需本模块的类。
"""
from __future__ import annotations

import time


class SimClock:
    """虚拟时钟：零真实等待，可复现。"""

    def __init__(self, start_ms: int = 0) -> None:
        self._now = int(start_ms)

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("虚拟时钟只能向前推进")
        self._now += int(ms)
        return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(int(round(seconds * 1000)))


class WallClock:
    """真实单调时钟，毫秒分辨率。"""

    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
