"""领域事件 / 命令 / 端口结果的数据模型（无业务逻辑，纯数据）。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    """进入确定性核心的事件种类。"""

    BOOT_OK = "boot_ok"
    BOOT_FAULT = "boot_fault"
    BUTTON = "button"
    MCU_LINE = "mcu_line"
    MCU_STATE = "mcu_state"
    MCU_SETTLED = "mcu_settled"
    MCU_ERROR = "mcu_error"
    MCU_FAULT = "mcu_fault"
    REC_STOP = "rec_stop"
    REC_CANCEL = "rec_cancel"
    AI_DONE = "ai_done"
    AI_FAIL = "ai_fail"
    AUDIO_FAIL = "audio_fail"
    PLAY_DONE = "play_done"
    PERFORM_DONE = "perform_done"
    SAFETY_TRIP = "safety_trip"
    FAULT_CLEAR = "fault_clear"
    IDLE_TICK = "idle_tick"
    ROLE_CHANGED = "role_changed"
    STATUS_TICK = "status_tick"
    NET_RESULT = "net_result"
    SHUTDOWN = "shutdown"


class CommandKind(StrEnum):
    """核心下发给领域工作线程的命令种类。"""

    ARM = "arm"
    FRAME = "frame"
    STOP = "stop"
    PLAY_ACTION = "play_action"
    STOP_MOTION = "stop_motion"
    RECORD_START = "record_start"
    RECORD_STOP = "record_stop"
    PLAY_AUDIO = "play_audio"
    STOP_AUDIO = "stop_audio"
    VOLUME_STEP = "volume_step"  # 音量键：档位进一档（内部迁移，不改变状态）
    NET_REQUEST = "net_request"
    PERSIST = "persist"
    NOTIFY = "notify"


@dataclass(frozen=True, slots=True)
class Event:
    """不可变事件。``generation`` 用于丢弃换角色/取消后的迟到回包。"""

    kind: EventKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    ts_ms: int = 0
    generation: int = 0


@dataclass(frozen=True, slots=True)
class Command:
    """核心下发的不可变命令。"""

    kind: CommandKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    generation: int = 0


@dataclass(frozen=True, slots=True)
class PortResult:
    """端口（串口/音频/网络）异步结果的三态封装。"""

    ok: bool
    value: Any = None
    error: str | None = None
    kind: EventKind | None = None

    @classmethod
    def success(cls, value: Any = None, kind: EventKind | None = None) -> PortResult:
        return cls(ok=True, value=value, kind=kind)

    @classmethod
    def failure(cls, error: str, kind: EventKind | None = None) -> PortResult:
        return cls(ok=False, error=error, kind=kind)
