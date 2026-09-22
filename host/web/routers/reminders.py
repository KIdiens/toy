"""提醒路由：``GET`` 列表、``POST`` 创建（201）、``DELETE /{id}`` 取消。

- 存储 UTC、展示 ``Asia/Shanghai``；模糊时刻由工具层返回 422 澄清。
- 创建幂等：带 ``idempotency_key`` 的重复提交不会重复排程（M8）。
- 取消是软取消（``status=cancelled``），记录保留可回溯。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["reminders"])


@router.get("/reminders")
def list_reminders(ctx: ContextDep, profile_id: str | None = None) -> dict[str, Any]:
    """查询提醒（按档案）。"""
    rows = ctx.list_reminders(profile_id=profile_id)
    return schemas.ok([schemas.ReminderOut(**r).model_dump() for r in rows])


@router.post("/reminders", status_code=201)
def create_reminder(payload: schemas.ReminderCreateRequest, ctx: ContextDep) -> dict[str, Any]:
    """创建持久提醒；仅在数据库事务成功后确认。"""
    result = ctx.create_reminder(payload.model_dump())
    return schemas.ok(schemas.ReminderOut(**result).model_dump())


@router.delete("/reminders/{id}")
def cancel_reminder(ctx: ContextDep, id: str) -> dict[str, Any]:
    """取消提醒（软取消，保留记录）。"""
    result = schemas.ReminderCancelled(**ctx.cancel_reminder(id))
    return schemas.ok(result.model_dump())
