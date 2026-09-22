"""三次平滑插值 ``s(u) = 3u^2 - 2u^3``。

与冻结基线 ``software/motion.py::sample`` 逐字对齐（架构 §5 兼容契约）：
``t<=0`` 取首帧、越界取末帧，边界内对每轴做 ``round(a + (b-a)*s)``。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def smoothstep(u: float) -> float:
    """标准 smoothstep：``3u^2 - 2u^3``（首末速度为 0，避免启停顿挫）。"""
    return u * u * (3 - 2 * u)


def sample(action: Mapping[str, Any], t_ms: int) -> list[int]:
    """按经过毫秒采样目标脉宽（**目标值**，非实测位置）。"""
    frames: Sequence[Mapping[str, Any]] = action["frames"]
    if t_ms <= 0:
        return list(frames[0]["us"])
    for left, right in zip(frames, frames[1:], strict=False):
        if t_ms <= right["t_ms"]:
            u = (t_ms - left["t_ms"]) / (right["t_ms"] - left["t_ms"])
            s = smoothstep(u)
            return [round(a + (b - a) * s) for a, b in zip(left["us"], right["us"], strict=False)]
    return list(frames[-1]["us"])
