"""角色路由：``GET /roles`` 列表、``PATCH /roles/active`` 切换。

切换即 ``session_generation += 1``（AC-15）；当前状态不允许切换时由
``AppContext.switch_active_role`` 抛 ``ConflictError`` → HTTP 409。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["roles"])


@router.get("/roles")
def list_roles(ctx: ContextDep) -> dict[str, Any]:
    """可用角色及配置版本。"""
    items = [schemas.RoleSummary(**s).model_dump() for s in ctx.role_summaries()]
    return schemas.ok(items)


@router.patch("/roles/active")
def set_active_role(payload: schemas.ActiveRolePatch, ctx: ContextDep) -> dict[str, Any]:
    """切换当前角色；正在运动/录音时返回 409。"""
    return schemas.ok(ctx.switch_active_role(payload.role_id))
