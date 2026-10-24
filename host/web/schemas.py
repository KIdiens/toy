"""Pydantic 请求/响应模型：**唯一出入参定义**（architecture §4 / SPEC §5）。

- 请求体全部用模型声明式校验（``request_id`` 强制、字段白名单、范围约束）。
- 响应统一信封 ``{code, data, message}``；``ok()`` / ``error()`` 是唯一构造点。
- 密钥类字段（``wifi 密码`` 等）**绝不出现**在任何模型里（不回显）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

STATE_NAMES = ("SELF_TEST", "IDLE", "RECORDING", "THINKING", "SPEAKING", "PERFORMING",
               "FAULT_LOCKED")
FAULT_CODES = ("INTERLOCK", "TIMEOUT", "PWM_INIT", "SAFETY_TRIP", "PROVIDER", "STORAGE")
VOLUME_LEVELS = ("low", "mid", "high")
LANGUAGES = ("Auto", "zh", "en")


def ok(data: Any = None) -> dict[str, Any]:
    """成功信封。``code=0``、``message`` 为空串。"""
    return {"code": 0, "data": data, "message": ""}


def error(code: int, message: str) -> dict[str, Any]:
    """错误信封（人类可读、无占位空话）。"""
    return {"code": code, "data": None, "message": message}


# ---------------------------------------------------------------- 请求模型
class ActiveRolePatch(BaseModel):
    request_id: str = Field(min_length=1)
    role_id: int = Field(ge=1, le=3)


class ActionValidateRequest(BaseModel):
    request_id: str = Field(min_length=1)
    action: dict[str, Any]


class ActionPlayRequest(BaseModel):
    request_id: str = Field(min_length=1)
    role_id: int = Field(ge=1, le=3)
    action_id: str = Field(min_length=1, max_length=60)
    action_version: str | None = None
    action: dict[str, Any] | None = None
    replace_current: bool = False


class StopRequest(BaseModel):
    request_id: str = Field(min_length=1)


class ReminderCreateRequest(BaseModel):
    request_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=200)
    due_at_utc: str = Field(min_length=1)
    timezone: str = "Asia/Shanghai"
    repeat_rule: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


class SettingsPatch(BaseModel):
    request_id: str = Field(min_length=1)
    volume_level: Literal["low", "mid", "high"] | None = None
    language: Literal["Auto", "zh", "en"] | None = None
    memory_enabled: bool | None = None
    content_rating_max: str | None = None


class PersonaOut(BaseModel):
    """人设的 **web 层镜像**（6 个字段与 ``PersonaConfig`` 一一对应）。

    刻意不复用领域模型、也不在这里写任何值约束（长度、条数、``max_chars`` 范围）：
    1. 分层习惯 —— web schema 是契约形状，领域模型的约束可以独立演化；
    2. **功能性原因** —— 若在这里加约束，FastAPI 会在进入处理器**之前**返回 422，
       ``POST /persona/preview`` 的「永远 200 + 内联错误」就失效了：UI 要拿的是
       逐字段红字，不是弹窗。所以全部值校验都推迟到处理器里由 ``PersonaConfig``
       做（唯一真源），两个端点共用同一份判定，不会出现「预览说行、保存说不行」。

    代价（登记误伤面）：字段类型错的请求（如 ``max_chars: "abc"``）仍会被 FastAPI
    在入口挡成 422 —— 那是「body 不是这个形状」，不是「内容非法」。
    """

    archetype: str = ""
    tone: str = ""
    speech_style: str = ""
    catchphrases: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    max_chars: int = 100


# ---------------------------------------------------------------- 响应模型
class Fault(BaseModel):
    code: Literal["INTERLOCK", "TIMEOUT", "PWM_INIT", "SAFETY_TRIP", "PROVIDER", "STORAGE"]
    detail: str


class ActionProgress(BaseModel):
    action_id: str
    seq: int = 0
    progress_ms: int = 0


class MotionTarget(BaseModel):
    """成功写入运动链路的目标，非 MCU ACK 或实测位置。"""

    source: Literal["sent_frame"]
    role_id: int = Field(ge=1, le=3)
    config_version: str
    action_id: str
    seq: int = Field(ge=0)
    progress_ms: int = Field(ge=0)
    target_us: list[int] = Field(min_length=4, max_length=4)
    valid_for_ms: int = Field(gt=0, le=1000)


class AxisLimits(BaseModel):
    role_id: int = Field(ge=1, le=3)
    config_version: str
    min_us: int
    max_us: int
    neutral_us: int


class Status(BaseModel):
    state: str
    mcu_armed: bool
    docked: bool
    estop_healthy: bool
    role_id: int
    session_generation: int
    network_online: bool
    fault: Fault | None = None
    action: ActionProgress | None = None
    worker_alive: bool = True
    worker_faults: int = 0
    target: MotionTarget | None = None
    axis_limits: AxisLimits | None = None


class RoleSummary(BaseModel):
    role_id: int
    name: str
    config_version: str
    tts_voice: str = ""
    language: str = "Auto"
    # ``None`` = 该角色没填人设（旧角色包）。不能用空对象表达「没填」：
    # 空对象会被编译成逐字段跳过的残句，两者语义不同。
    persona: PersonaOut | None = None


class PersonaSaveResult(BaseModel):
    role_id: int
    persona: PersonaOut | None = None
    persona_preview: str


class ValidateError(BaseModel):
    path: str
    message: str


class ValidateResult(BaseModel):
    valid: bool
    errors: list[ValidateError] = Field(default_factory=list)


class PersonaPreviewResult(BaseModel):
    """预览结果：**永远 200**，非法内容走 ``errors`` 内联返回（UI 不弹窗）。"""

    valid: bool
    errors: list[ValidateError] = Field(default_factory=list)
    compiled: str | None = None


class PlayResult(BaseModel):
    scheduled: bool
    action_id: str
    seq: int = 0


class MemoryOut(BaseModel):
    id: int
    profile_id: str
    kind: str
    value: str
    confirmed: bool
    created_at: str
    expires_at: str | None = None


class ReminderOut(BaseModel):
    id: str
    profile_id: str
    text: str
    # 仓储层该列可空（``due_at_utc: str | None``），文档声明 ``format: date-time``。
    # 空串不是合法 date-time，所以「没有值」必须是 ``None``，由 openapi 的
    # ``nullable: true`` 表达 —— 若这里留成 ``str``，DTO 的None 会直接 500。
    due_at_utc: str | None = None
    timezone: str
    repeat_rule: str | None = None
    status: str
    last_fired_at: str | None = None
    idempotency_key: str | None = None


class SettingsOut(BaseModel):
    volume_level: str = "mid"
    language: str = "Auto"
    memory_enabled: bool = True
    content_rating_max: str = "kid"
    wifi_ssid: str = ""
    sim_mode: bool = False


class ActionFile(BaseModel):
    name: str
    active: bool = False


class StopResult(BaseModel):
    stopped: bool


class ActiveRoleResult(BaseModel):
    role_id: int
    config_version: str


class MemoryDeleted(BaseModel):
    deleted: bool


class ReminderCancelled(BaseModel):
    cancelled: bool


class ContentImportResult(BaseModel):
    role_id: int
    config_version: str
    files: int
