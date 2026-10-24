"""动作路由：校验 / 播放 / 停止 + 可编辑动作文件列表。

- ``POST /actions/play`` 默认拒绝叠加（``replace_current=False`` 时正在播放 → 409，AC-16）。
- 播放必须带预期 ``role_id``：与当前角色不符即拒绝（防网页缓存把上一角色动作发给新公仔）。
- ``GET /actions/files`` 是**新增只读端点**（见 docs/SPEC-host-mvp.md 变更记录），
  返回当前角色可编辑动作文件名与激活指针，供编排器填充文件列表。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["actions"])


@router.get("/actions/files")
def list_action_files(ctx: ContextDep) -> dict[str, Any]:
    """当前角色动作文件；``active`` 为服务端指针，页面高亮以已加载来源为准。"""
    files = [schemas.ActionFile(**item).model_dump() for item in ctx.action_files()]
    return schemas.ok(files)


@router.post("/actions/validate")
def validate_action(payload: schemas.ActionValidateRequest, ctx: ContextDep) -> dict[str, Any]:
    """只校验动作文档，不移动硬件。"""
    result = schemas.ValidateResult(**ctx.validate_action(payload.action))
    return schemas.ok(result.model_dump())


@router.get("/actions/files/{action_id}")
def get_action_document(action_id: str, ctx: ContextDep,
                        role_id: int = Query(ge=1, le=3)) -> dict[str, Any]:
    """读取预期角色的完整动作；不修改设备当前动作指针。"""
    return schemas.ok(ctx.action_document(role_id=role_id, action_id=action_id))


@router.post("/actions/play")
def play_action(payload: schemas.ActionPlayRequest, ctx: ContextDep) -> dict[str, Any]:
    """当前角色匹配且安全检查通过才调度播放；冲突返回 409。"""
    result = ctx.play_action(
        role_id=payload.role_id,
        action_id=payload.action_id,
        action_version=payload.action_version,
        action=payload.action,
        replace_current=payload.replace_current,
    )
    return schemas.ok(schemas.PlayResult(**result).model_dump())


@router.post("/actions/stop")
def stop_action(payload: schemas.StopRequest, ctx: ContextDep) -> dict[str, Any]:
    """立即取消队列并通知 MCU 停机。"""
    del payload  # request_id 已在模型层强制校验，审计由网关日志负责
    return schemas.ok(schemas.StopResult(**ctx.stop_action()).model_dump())
