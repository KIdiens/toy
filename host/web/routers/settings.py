"""设置路由：``GET /settings`` 读取、``PATCH /settings`` 修改。

密码类字段一律不回显；只接受契约内的白名单字段（模型层已限制）。
设置持久化到 SQLite；音量同时下发到播放器。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings(ctx: ContextDep) -> dict[str, Any]:
    """音量、语言、记忆开关与配网（仅回显 SSID，不含密码）。"""
    return schemas.ok(schemas.SettingsOut(**ctx.get_settings()).model_dump())


@router.patch("/settings")
def patch_settings(payload: schemas.SettingsPatch, ctx: ContextDep) -> dict[str, Any]:
    """只提交变化字段；返回更新后的完整设置。"""
    patch = payload.model_dump(exclude_none=True, exclude={"request_id"})
    return schemas.ok(schemas.SettingsOut(**ctx.patch_settings(patch)).model_dump())
