"""依赖注入：路由经 ``Depends`` 取得运行期上下文（``app.state.context``）。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from host.adapters.ports import AppContext


def get_context(request: Request) -> AppContext:
    """从 app.state 取上下文（由 ``create_app`` 注入）。"""
    return request.app.state.context


ContextDep = Annotated[AppContext, Depends(get_context)]
