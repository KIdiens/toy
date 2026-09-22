"""功能开关路由：``GET /features``。

返回开关映射（key -> enabled）；``provider`` 为当前 AI 后端标识（stub / qwen），
供前端如实展示桩与真实云端档位（openapi.yaml 的 /features 已同步该字段）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["features"])


@router.get("/features")
def list_features(ctx: ContextDep) -> dict[str, Any]:
    """当前用户的功能开关状态。"""
    return schemas.ok(ctx.features())
