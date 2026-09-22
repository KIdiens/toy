"""动作校验**单一真源**，与冻结基线 ``software/motion.py::validate`` 逐字对齐。

``tests/contract/test_motion_parity.py`` 用 fuzz 语料比对两者输出（旧文件可达时
运行，不可达则 ``skip``）。任何规则改动都必须先改本文件并回写 Spec 兼容契约。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

LOW, HIGH = 1400, 1600
MAX_SPEED = 300.0
MIN_FRAMES, MAX_FRAMES = 2, 1000
MAX_T_MS = 60000
VALID_ROLES = (1, 2, 3)


def validate(action: Mapping[str, Any]) -> Mapping[str, Any]:
    """检查时间、数值、幅度和三次插值的峰值速度；非法抛 ``ValueError``。"""
    if (
        type(action.get("schema")) is not int
        or action.get("schema") != 1
        or type(action.get("role_id")) is not int
        or action.get("role_id") not in VALID_ROLES
    ):
        raise ValueError("动作格式版本或角色编号错误")
    frames = action.get("frames")
    if not isinstance(frames, list) or not MIN_FRAMES <= len(frames) <= MAX_FRAMES:
        raise ValueError("关键帧数量须为 2 至 1000")
    previous = None
    for frame in frames:
        t, us = frame.get("t_ms"), frame.get("us")
        if type(t) is not int or not 0 <= t <= MAX_T_MS:
            raise ValueError("时间须为 0 至 60000 的整数毫秒")
        if not isinstance(us, list) or len(us) != 4:
            raise ValueError("每帧必须有四个轴")
        if any(
            type(x) not in (int, float) or not math.isfinite(x) or not LOW <= x <= HIGH
            for x in us
        ):
            raise ValueError("脉宽必须在保守范围 1400 至 1600 微秒内")
        if previous:
            dt = (t - previous["t_ms"]) / 1000
            if dt <= 0:
                raise ValueError("时间必须严格递增")
            if any(
                1.5 * abs(a - b) / dt > MAX_SPEED
                for a, b in zip(us, previous["us"], strict=False)
            ):
                raise ValueError("动作峰值速度超过 300 微秒每秒")
        previous = frame
    if frames[0]["t_ms"] != 0 or frames[0]["us"] != [1500] * 4:
        raise ValueError("第一帧必须为零时刻和四轴中位")
    if frames[-1]["us"] != [1500] * 4:
        raise ValueError("起步示例要求末帧回到中位")
    return action
