"""提醒工具：创建 / 取消 + 调度与补发 + 幂等键（M8）。

- **幂等键**：``create`` 带 ``idempotency_key``，重复创建被 UNIQUE 挡下 → 返回
  ``deduplicated=True``（上层据「已存在」处理），绝不重复排程。
- **只触发一次**：``ReminderScheduler.fire_due`` 用 ``claim_due`` 条件更新，只有真正把
  ``pending`` 改成 ``fired`` 的那一次会返回并通知，重启/竞态下不重复触发。
- **时间不可靠**：``now_provider`` 拿不到时间时暂停提醒并明确报错（§10 时间不可靠）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host.ai.tools import ParamSpec, ToolRegistry
from host.core.clock import format_iso, parse_iso
from host.storage import reminders_repo
from host.storage.errors import UniqueViolation

DEFAULT_TIMEZONE = "Asia/Shanghai"
TIME_UNKNOWN_ERROR = "时间不可靠，已暂停提醒，请先校时"
NowProvider = Callable[[], str | None]


def _normalize_due(text: str) -> str:
    try:
        return format_iso(parse_iso(text))
    except ValueError as exc:
        raise ValueError(f"非法时间: {text}") from exc


def create(
    db,
    *,
    text: str,
    due_at_utc: str,
    now: str,
    timezone: str = DEFAULT_TIMEZONE,
    profile_id: int | None = None,
    repeat_rule: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    try:
        reminder_id = reminders_repo.create(
            db,
            text=text,
            due_at_utc=_normalize_due(due_at_utc),
            timezone=timezone,
            profile_id=profile_id,
            repeat_rule=repeat_rule,
            idempotency_key=idempotency_key,
            now=now,
        )
    except UniqueViolation:
        return {"ok": True, "id": None, "deduplicated": True}
    return {"ok": True, "id": reminder_id, "deduplicated": False}


def cancel(db, reminder_id: int) -> dict[str, Any]:
    return {"ok": True, "cancelled": reminders_repo.cancel(db, reminder_id)}


@dataclass(slots=True)
class ReminderScheduler:
    """到期领取 + 幂等触发；``now_provider`` 返回 ISO 文本或 ``None``（时间不可靠）。"""

    db: object
    now_provider: NowProvider
    notify: Callable[[Any], None] | None = None
    timezone: str = DEFAULT_TIMEZONE

    def due(self) -> list[Any]:
        now = self.now_provider()
        if not now:
            return []
        return reminders_repo.list_due(self.db, now_utc=now)

    def fire_due(self) -> list[Any]:
        now = self.now_provider()
        if not now:
            return []
        fired: list[Any] = []
        for reminder in reminders_repo.list_due(self.db, now_utc=now):
            if reminders_repo.claim_due(self.db, reminder.id, fired_at=now):
                fired.append(reminder)
                if self.notify is not None:
                    self.notify(reminder)
        return fired


def register(
    registry: ToolRegistry,
    db,
    *,
    now_provider: NowProvider,
    profile_id: int | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> None:
    """把 ``create_reminder`` / ``cancel_reminder`` 注册进白名单。"""

    def create_handler(args: dict[str, Any]) -> dict[str, Any]:
        now = now_provider()
        if not now:
            return {"ok": False, "error": TIME_UNKNOWN_ERROR}
        try:
            return create(
                db,
                text=str(args["text"]),
                due_at_utc=str(args["due_at_utc"]),
                now=now,
                timezone=str(args.get("timezone", timezone)),
                profile_id=profile_id,
                idempotency_key=args.get("idempotency_key"),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def cancel_handler(args: dict[str, Any]) -> dict[str, Any]:
        return cancel(db, int(args["id"]))

    registry.register(
        "create_reminder",
        "创建一个定时提醒（到点提醒孩子）。",
        (
            ParamSpec("text", str, required=True, pattern=r".{1,200}"),
            ParamSpec("due_at_utc", str, required=True, pattern=r"\d{4}-\d{2}-\d{2}T.+"),
            ParamSpec("idempotency_key", str, required=False, pattern=r".{1,64}"),
            ParamSpec("timezone", str, required=False, pattern=r"[A-Za-z]+/[A-Za-z_]+"),
        ),
        create_handler,
    )
    registry.register(
        "cancel_reminder",
        "取消一个已存在的提醒。",
        (ParamSpec("id", int, required=True, minimum=1),),
        cancel_handler,
    )
