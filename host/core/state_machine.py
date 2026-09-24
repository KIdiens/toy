"""显式状态表状态机（架构 §7.1 / §7.1.1 的可穷举落地）。

设计要点：

- 7 状态、约 20 条迁移，**表即规格**，可穷举测试。
- ``can_fire`` 返回 ``(ok, reason)``；非法迁移**不抛未捕获异常**，返回拒绝原因并计数。
- ``SAFETY_TRIP`` 是**唯一**允许从任意态抢占的迁移（已锁定态除外）。
- 每次进入 ``IDLE`` 或角色切换：``session_generation += 1``，用于丢弃迟到回包。
- 三按键行为表 ``BUTTON_TABLE`` 逐格落地 §7.1.1；音量键是**内部迁移**（不改状态）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

BUTTON_DIALOG = 1
BUTTON_PERFORM = 2
BUTTON_VOLUME = 3

BUTTON_NOOP = "noop"  # 显式无行为（可解释，非错误）
BUTTON_DISCARD = "discard"  # 未就绪/锁定态，按键被丢弃

IDLE_TICK_MIN_S = 120


class State(StrEnum):
    SELF_TEST = "SELF_TEST"
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    PERFORMING = "PERFORMING"
    FAULT_LOCKED = "FAULT_LOCKED"


class Trigger(StrEnum):
    BOOT_OK = "BOOT_OK"
    BOOT_FAULT = "BOOT_FAULT"
    DIALOG_PRESS = "DIALOG_PRESS"
    REC_STOP = "REC_STOP"
    REC_CANCEL = "REC_CANCEL"
    AI_DONE = "AI_DONE"
    AI_FAIL = "AI_FAIL"
    PLAY_DONE = "PLAY_DONE"
    PERFORM_PRESS = "PERFORM_PRESS"
    PERFORM_DONE = "PERFORM_DONE"
    SAFETY_TRIP = "SAFETY_TRIP"
    FAULT_CLEAR = "FAULT_CLEAR"
    IDLE_TICK = "IDLE_TICK"
    VOLUME_PRESS = "VOLUME_PRESS"


S = State
T = Trigger

TRANSITIONS: dict[tuple[State, Trigger], State] = {
    (S.SELF_TEST, T.BOOT_OK): S.IDLE,
    (S.SELF_TEST, T.BOOT_FAULT): S.FAULT_LOCKED,
    (S.IDLE, T.DIALOG_PRESS): S.RECORDING,
    (S.IDLE, T.PERFORM_PRESS): S.PERFORMING,
    (S.IDLE, T.IDLE_TICK): S.PERFORMING,
    (S.IDLE, T.VOLUME_PRESS): S.IDLE,
    (S.RECORDING, T.DIALOG_PRESS): S.THINKING,  # 对话键结束录音
    (S.RECORDING, T.REC_STOP): S.THINKING,
    (S.RECORDING, T.PERFORM_PRESS): S.IDLE,  # 表演键取消录音
    (S.RECORDING, T.REC_CANCEL): S.IDLE,
    (S.THINKING, T.DIALOG_PRESS): S.IDLE,  # 取消本轮，generation+1
    (S.THINKING, T.PERFORM_PRESS): S.PERFORMING,
    (S.THINKING, T.AI_DONE): S.SPEAKING,
    (S.THINKING, T.AI_FAIL): S.IDLE,
    (S.THINKING, T.VOLUME_PRESS): S.THINKING,
    (S.SPEAKING, T.DIALOG_PRESS): S.RECORDING,  # 先停播再录
    (S.SPEAKING, T.PERFORM_PRESS): S.IDLE,
    (S.SPEAKING, T.PLAY_DONE): S.IDLE,
    (S.SPEAKING, T.VOLUME_PRESS): S.SPEAKING,
    (S.PERFORMING, T.DIALOG_PRESS): S.RECORDING,  # 先停动作/播放再录
    (S.PERFORMING, T.PERFORM_PRESS): S.IDLE,
    (S.PERFORMING, T.PERFORM_DONE): S.IDLE,
    (S.PERFORMING, T.VOLUME_PRESS): S.PERFORMING,
    (S.FAULT_LOCKED, T.FAULT_CLEAR): S.SELF_TEST,
}

WILDCARD: dict[Trigger, State] = {T.SAFETY_TRIP: S.FAULT_LOCKED}

BUTTON_TABLE: dict[tuple[State, int], str] = {
    (S.SELF_TEST, BUTTON_DIALOG): BUTTON_DISCARD,
    (S.SELF_TEST, BUTTON_PERFORM): BUTTON_DISCARD,
    (S.SELF_TEST, BUTTON_VOLUME): BUTTON_DISCARD,
    (S.IDLE, BUTTON_DIALOG): T.DIALOG_PRESS.value,
    (S.IDLE, BUTTON_PERFORM): T.PERFORM_PRESS.value,
    (S.IDLE, BUTTON_VOLUME): T.VOLUME_PRESS.value,
    (S.RECORDING, BUTTON_DIALOG): T.REC_STOP.value,
    (S.RECORDING, BUTTON_PERFORM): T.REC_CANCEL.value,
    (S.RECORDING, BUTTON_VOLUME): BUTTON_NOOP,
    (S.THINKING, BUTTON_DIALOG): T.DIALOG_PRESS.value,
    (S.THINKING, BUTTON_PERFORM): T.PERFORM_PRESS.value,
    (S.THINKING, BUTTON_VOLUME): T.VOLUME_PRESS.value,
    (S.SPEAKING, BUTTON_DIALOG): T.DIALOG_PRESS.value,
    (S.SPEAKING, BUTTON_PERFORM): T.PERFORM_PRESS.value,
    (S.SPEAKING, BUTTON_VOLUME): T.VOLUME_PRESS.value,
    (S.PERFORMING, BUTTON_DIALOG): T.DIALOG_PRESS.value,
    (S.PERFORMING, BUTTON_PERFORM): T.PERFORM_PRESS.value,
    (S.PERFORMING, BUTTON_VOLUME): T.VOLUME_PRESS.value,
    (S.FAULT_LOCKED, BUTTON_DIALOG): BUTTON_DISCARD,
    (S.FAULT_LOCKED, BUTTON_PERFORM): BUTTON_DISCARD,
    (S.FAULT_LOCKED, BUTTON_VOLUME): BUTTON_DISCARD,
}

Guard = Callable[[Mapping[str, Any]], "tuple[bool, str]"]


def _guard_boot_ok(context: Mapping[str, Any]) -> tuple[bool, str]:
    if context.get("mcu_ready") is False:
        return False, "MCU 未就绪"
    if context.get("role_stable") is False:
        return False, "角色未稳定"
    return True, ""


def _guard_idle_tick(context: Mapping[str, Any]) -> tuple[bool, str]:
    if context.get("role_present") is False:
        return False, "未装公仔不启用待机小动作"
    if context.get("night") is True:
        return False, "夜间不主动发声"
    seconds = context.get("idle_seconds")
    if seconds is not None and seconds < IDLE_TICK_MIN_S:
        return False, f"闲置不足 {IDLE_TICK_MIN_S}s"
    return True, ""


GUARDS: dict[tuple[State, Trigger], Guard] = {
    (S.SELF_TEST, T.BOOT_OK): _guard_boot_ok,
    (S.IDLE, T.IDLE_TICK): _guard_idle_tick,
}


def trigger_for_button(state: State, button: int) -> str:
    """按键 → 该状态下的动作（触发器名 / ``noop`` / ``discard``）。"""
    try:
        return BUTTON_TABLE[(State(state), int(button))]
    except KeyError as exc:
        raise ValueError(f"未知的 状态×按键 组合: {state.name} x {button}") from exc


@dataclass(frozen=True, slots=True)
class TransitionResult:
    ok: bool
    reason: str
    source: State
    dest: State
    generation: int
    trigger: Trigger

    @property
    def changed(self) -> bool:
        return self.ok and self.source != self.dest


class SessionStateMachine:
    """持有当前状态与 ``session_generation`` 的单线程状态机。"""

    def __init__(
        self,
        *,
        initial: State = State.SELF_TEST,
        generation: int = 0,
        logger=None,
    ) -> None:
        self.state = State(initial)
        self.generation = int(generation)
        self.substate: str | None = None
        self.logger = logger
        self.rejections = 0
        self.transitions = 0
        self.history: list[TransitionResult] = []

    def can_fire(
        self, trigger: Trigger | str, *, context: Mapping[str, Any] | None = None
    ) -> tuple[bool, str]:
        trigger = Trigger(trigger)
        if trigger in WILDCARD:
            if self.state is WILDCARD[trigger]:
                return False, f"已处于 {self.state.value}"
            return True, ""
        key = (self.state, trigger)
        if key not in TRANSITIONS:
            return False, f"状态 {self.state.value} 不接受触发 {trigger.value}"
        guard = GUARDS.get(key)
        if guard is not None:
            return guard(context or {})
        return True, ""

    def destination(self, trigger: Trigger | str) -> State | None:
        """**预判**迁移目标态而不改变状态；不可迁移返回 ``None``。

        给「先校验、后调用 ``apply``」的入口用：调用方据此判断本次动作是否会被接受，
        避免出现「先改了状态、再抛 409 拒绝」这种带副作用的拒绝。
        """
        trigger = Trigger(trigger)
        ok, _reason = self.can_fire(trigger)
        if not ok:
            return None
        if trigger in WILDCARD:
            return WILDCARD[trigger]
        return TRANSITIONS[(self.state, trigger)]

    def fire(
        self, trigger: Trigger | str, *, context: Mapping[str, Any] | None = None
    ) -> TransitionResult:
        trigger = Trigger(trigger)
        ok, reason = self.can_fire(trigger, context=context)
        if not ok:
            self.rejections += 1
            if self.logger is not None:
                self.logger.info(
                    "transition rejected: %s in %s (%s)",
                    trigger.value,
                    self.state.value,
                    reason,
                )
            return TransitionResult(False, reason, self.state, self.state,
                                    self.generation, trigger)
        source = self.state
        dest = WILDCARD[trigger] if trigger in WILDCARD else TRANSITIONS[(source, trigger)]
        self.state = dest
        self.transitions += 1
        if dest is S.IDLE and source is not S.IDLE:
            self.generation += 1
        if dest is S.IDLE:
            self.substate = "NO_ROLE" if (context or {}).get("no_role") else None
        result = TransitionResult(True, "", source, dest, self.generation, trigger)
        self.history.append(result)
        return result

    def bump_generation(self) -> int:
        """取消本轮 / 换角色：generation +1，使迟到回包失效。"""
        self.generation += 1
        return self.generation

    def switch_role(self) -> int:
        """换角色：等价于 §7.1「角色切换 generation+1」。"""
        return self.bump_generation()

    def reset(self, *, initial: State = State.SELF_TEST) -> None:
        self.state = State(initial)
        self.substate = None
        self.history.clear()
