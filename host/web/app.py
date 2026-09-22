"""FastAPI 装配 + 静态挂载：**只装配，无业务逻辑**（architecture §2/§4）。

- 挂载契约（前端已按此实现，必须对齐）：
  ``/`` → ``static/index.html``；``/editor`` ``/memories`` ``/reminders`` ``/settings``
  → 对应 HTML；``/static/*`` → ``host/web/static/``。
- 错误映射：``ConflictError``→409、``ClarifyError``→422、``RecordNotFound``→404、
  其它 ``HostError``→500；响应一律为统一信封 ``{code,data,message}``。
- ``dev_sim`` 路由**仅在 SIM 档位挂载**，生产不注册。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from host.adapters.ports import AppContext
from host.core.errors import (
    ClarifyError,
    ConflictError,
    HostError,
    RecordNotFound,
    StorageError,
)
from host.web import schemas
from host.web.routers import (
    actions,
    content,
    dev_sim,
    events,
    features,
    memories,
    reminders,
    roles,
    settings,
    status,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
API_PREFIX = "/api/v1"
PAGE_ROUTES: dict[str, str] = {
    "/": "index.html",
    "/editor": "editor.html",
    "/memories": "memories.html",
    "/reminders": "reminders.html",
    "/settings": "settings.html",
}
_API_ROUTERS = (status.router, roles.router, actions.router, content.router,
                memories.router, reminders.router, settings.router, features.router,
                events.router)


def create_app(context: AppContext) -> FastAPI:
    """用给定上下文装配 FastAPI 应用。"""
    app = FastAPI(title="AI 角色玩具 · 主机管理 API", version="1.0.0")
    app.state.context = context
    _register_error_handlers(app)
    for api_router in _API_ROUTERS:
        app.include_router(api_router, prefix=API_PREFIX)
    if context.simulate:
        app.include_router(dev_sim.router, prefix=API_PREFIX)
    _mount_static(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConflictError)
    async def _on_conflict(_request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=schemas.error(40900, str(exc)))

    @app.exception_handler(ClarifyError)
    async def _on_clarify(_request, exc: ClarifyError) -> JSONResponse:
        return JSONResponse(status_code=422, content=schemas.error(42200, str(exc)))

    @app.exception_handler(RecordNotFound)
    async def _on_not_found(_request, exc: RecordNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content=schemas.error(40400, str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _on_invalid(_request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=schemas.error(42201, _describe(exc)))

    @app.exception_handler(StorageError)
    async def _on_storage(_request, exc: StorageError) -> JSONResponse:
        return JSONResponse(status_code=500, content=schemas.error(50001, str(exc)))

    @app.exception_handler(StarletteHTTPException)
    async def _on_http(_request, exc: StarletteHTTPException) -> JSONResponse:
        detail = str(exc.detail) if exc.detail else "请求失败"
        return JSONResponse(status_code=exc.status_code,
                            content=schemas.error(exc.status_code * 100, detail))

    @app.exception_handler(HostError)
    async def _on_host_error(_request, exc: HostError) -> JSONResponse:
        return JSONResponse(status_code=500, content=schemas.error(50000, str(exc)))


def _describe(exc: RequestValidationError) -> str:
    """把 FastAPI 校验错误压成人可读、无占位话术的一句话。"""
    skipped = ("body", "query", "path")
    parts: list[str] = []
    for err in exc.errors():
        location = ".".join(str(x) for x in err.get("loc", ()) if x not in skipped)
        message = err.get("msg", "非法取值")
        parts.append(f"{location or '请求'}: {message}")
    return "请求参数校验失败：" + "；".join(parts)


def _mount_static(app: FastAPI) -> None:
    if not STATIC_DIR.is_dir():
        return
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for route, filename in PAGE_ROUTES.items():
        target = STATIC_DIR / filename
        if target.is_file():
            app.get(route, include_in_schema=False)(_page_handler(target))


def _page_handler(target: Path):
    def handler() -> FileResponse:
        return FileResponse(target)

    return handler
