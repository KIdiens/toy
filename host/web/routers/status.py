"""``GET /status``：当前角色、网络、动作与故障（不返回任何密钥）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status(ctx: ContextDep) -> dict[str, Any]:
    """返回状态快照，经 ``Status`` 模型校验后装进统一信封。"""
    snapshot = schemas.Status(**ctx.status_snapshot())
    return schemas.ok(snapshot.model_dump())
