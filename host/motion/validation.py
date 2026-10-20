"""动作校验**单一真源**，与冻结基线 ``software/motion.py::validate`` 逐字对齐。

``tests/contract/test_motion_parity.py`` 用 fuzz 语料比对两者输出（旧文件可达时
运行，不可达则 ``skip``）。任何规则改动都必须先改本文件并回写 Spec 兼容契约。

**关于 ``min_us``/``max_us`` 两个可选参数**：基线契约**只管固件台架保守区间
1400–1600**，因此它们的默认值就是 ``LOW``/``HIGH`` —— 不传时本函数与冻结基线
**异常类型与消息文本都逐字一致**（契约测试比对的正是这两项）。
调用方若持有角色配置，**应当**传入该角色 ``role.json`` 声明的限幅：固件区间是所有
角色的公共兜底，比多数角色的限幅**宽**（role2 是 1420–1580、role3 是 1430–1570），
只查固件区间等于对这两个角色的机械限位不设防。
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
AXIS_COUNT = 4


def validate(action: Mapping[str, Any], *,
             min_us: int = LOW, max_us: int = HIGH) -> Mapping[str, Any]:
    """检查时间、数值、幅度和三次插值的峰值速度；非法抛 ``ValueError``。

    ``min_us``/``max_us`` 是**闭区间**（两端都算合法）—— ``content/roles`` 里
    role1 的 wave、role2 的 wave、role3 的 nod 三个现有文件都恰好取到 ``max_us``，
    用半开区间会一次性打死这三个已通过全部验收的动作。

    **限幅参数只允许收窄、不允许放宽**：``min_us``/``max_us`` 必须自身落在固件
    区间内。否则这个参数就成了「绕过固件台架保守值」的后门 —— 传 ``(0, 9999)``
    反而让校验比不传更松。角色 schema（``host/roles/schema.py``）已保证
    ``min_us``/``max_us`` 落在 ``FW_MIN_US..FW_MAX_US``，此处的守卫是防绕过
    schema 直调本函数。
    """
    if not (LOW <= min_us <= max_us <= HIGH):
        raise ValueError(
            f"限幅必须在固件保守区间 {LOW} 至 {HIGH} 微秒内且下限不高于上限"
        )
    # 形状先于内容：``action.get`` 只对 Mapping 成立。此前这里直接 ``action.get(...)``，
    # 传入 ``None`` / 列表会让 ``AttributeError`` 穿出校验层，一路逃成 HTTP 500，
    # 在表演键路径上打穿 motion worker（PERFORM_DONE 永不产生 → 永久 PERFORMING）。
    # 非 Mapping 属**域外输入**（冻结基线抛的是 AttributeError，不是契约），此处显式
    # 抛 ValueError —— 与其它非法输入同一失败语义，调用方按「内容非法」处理即可。
    if not isinstance(action, Mapping):
        raise ValueError(
            f"动作必须是对象（JSON object），实际是 {type(action).__name__}"
        )
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
    for index, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            # 消息带帧序号（从 1 计）与期望形状，内容作者才能定位到具体那一行。
            raise ValueError(
                f"第 {index + 1} 帧必须是对象（frames[{index}] 期望 JSON object，"
                f"实际是 {type(frame).__name__}）"
            )
        t, us = frame.get("t_ms"), frame.get("us")
        if type(t) is not int or not 0 <= t <= MAX_T_MS:
            raise ValueError("时间须为 0 至 60000 的整数毫秒")
        if not isinstance(us, list) or len(us) != AXIS_COUNT:
            raise ValueError("每帧必须有四个轴")
        if any(
            type(x) not in (int, float) or not math.isfinite(x) or not min_us <= x <= max_us
            for x in us
        ):
            # 默认区间沿用基线原文，确保不传参时与冻结基线**逐字一致**（契约测试比消息）。
            if (min_us, max_us) == (LOW, HIGH):
                raise ValueError("脉宽必须在保守范围 1400 至 1600 微秒内")
            raise ValueError(f"脉宽超出角色限幅 {min_us} 至 {max_us} 微秒")
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
