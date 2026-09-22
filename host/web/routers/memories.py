"""记忆路由：``GET /memories`` 查看、``DELETE /memories/{id}`` 删除。

删除是**硬删除**（单步事务）：删除后新会话不再注入该条（AC-13，见
``host.storage.memories_repo.delete`` 与 ``list_for_context``）。契约无批量端点，
前端「清空全部」逐个 DELETE 并如实汇报成功/失败。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["memories"])


@router.get("/memories")
def list_memories(
    ctx: ContextDep,
    profile_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """查看当前档案记忆（可按 ``kind`` 下推筛选）。"""
    rows = ctx.list_memories(profile_id=profile_id, kind=kind)
    return schemas.ok([schemas.MemoryOut(**m).model_dump() for m in rows])


@router.delete("/memories/{id}")
def delete_memory(ctx: ContextDep, id: int) -> dict[str, Any]:
    """删除单条记忆（同时使会话缓存/摘要中的对应内容失效）。"""
    result = schemas.MemoryDeleted(**ctx.delete_memory(id))
    return schemas.ok(result.model_dump())
