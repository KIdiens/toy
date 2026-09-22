"""统一单调时钟时间轴：按**经过时间**采样，禁止逐帧 ``sleep`` 累加。

规范（Spec §10 性能）：帧调度按单调时钟采样；``elapsed`` 由 ``clock.now_ms()``
与起点之差得出，误差不累积。动作结束后的保持阶段用 ``overshoot_ms`` 度量。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host.motion.interpolation import sample


class Timeline:
    """单个动作的时间轴视图（目标脉宽序列）。"""

    def __init__(self, action: Mapping[str, Any], clock) -> None:
        self.action = action
        self.clock = clock
        self.end_ms: int = int(action["frames"][-1]["t_ms"])
        self._start_ms: int | None = None

    def begin(self) -> None:
        self._start_ms = self.clock.now_ms()

    @property
    def started(self) -> bool:
        return self._start_ms is not None

    def elapsed_ms(self) -> int:
        if self._start_ms is None:
            return 0
        return max(0, self.clock.now_ms() - self._start_ms)

    def clamped_elapsed_ms(self) -> int:
        return min(self.elapsed_ms(), self.end_ms)

    def overshoot_ms(self) -> int:
        """末帧之后已经过的时间（用于「末帧保持 >=150ms」判定）。"""
        return max(0, self.elapsed_ms() - self.end_ms)

    def at_end(self) -> bool:
        return self.elapsed_ms() >= self.end_ms

    def frame(self) -> list[int]:
        """当前时刻的目标脉宽（四轴）。"""
        return sample(self.action, self.clamped_elapsed_ms())
