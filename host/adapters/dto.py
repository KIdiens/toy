"""适配层 DTO 转换：core/storage 结构 → web 出参字典。

纯函数、无业务规则、无 I/O；把仓储行类型与设置键值映射成契约形状（SPEC §5）。
布尔/枚举的**字符串解析**在这里收口（存储层只存字符串，不解释）。
"""
from __future__ import annotations

from typing import Any

from host.audio.player import DEFAULT_LEVEL, VOLUME_LEVELS

TRUE_VALUES = {"1", "true", "yes", "on"}


def as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def memory_dto(memory) -> dict[str, Any]:
    return {
        "id": int(memory.id),
        "profile_id": str(memory.profile_id),
        "kind": str(memory.kind),
        "value": str(memory.value),
        "confirmed": bool(memory.confirmed),
        "created_at": str(memory.created_at),
        "expires_at": memory.expires_at,
    }


def reminder_dto(reminder) -> dict[str, Any]:
    # `due_at_utc` 在仓储层是 ``str | None``（DDL 里该列可空），文档声明
    # ``format: date-time``。空串**不是**合法 RFC 3339 date-time——把 None 洗成 ""
    # 会让响应带一个非法字面量，按文档生成的客户端解析即炸。故此处保留 None，
    # 由``ReminderOut`` 与 openapi 的 ``nullable: true`` 表达「可能没有值」。
    return {
        "id": str(reminder.id),
        "profile_id": "" if reminder.profile_id is None else str(reminder.profile_id),
        "text": reminder.text or "",
        "due_at_utc": reminder.due_at_utc,
        "timezone": reminder.timezone or "",
        "repeat_rule": reminder.repeat_rule,
        "status": reminder.status or "pending",
        "last_fired_at": reminder.last_fired_at,
        "idempotency_key": reminder.idempotency_key,
    }


def settings_dto(values: dict[str, str], *, sim_mode: bool, wifi_ssid: str = "") -> dict[str, Any]:
    # 音量档位**白名单化**：库里可能残留非法值（人工改库、历史数据、外部工具写入）。
    # 运行期 Player 会回落到默认档，若出参原样回吐非法值，界面显示会与实际播放分叉。
    stored_volume = values.get("volume")
    return {
        "volume_level": stored_volume if stored_volume in VOLUME_LEVELS else DEFAULT_LEVEL,
        "language": values.get("language", "Auto"),
        "memory_enabled": as_bool(values.get("memory_enabled"), default=True),
        "content_rating_max": values.get("content_rating_max", "kid"),
        "wifi_ssid": wifi_ssid,
        "sim_mode": sim_mode,
    }
