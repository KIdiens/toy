"""实时事件流：``GET /events`` 升级为 WebSocket（openapi 101）。

服务端每 0.5s 采样一次状态快照，**仅在变化时**推送一帧，避免刷屏；
帧形状与前端 ``pages/status.js`` 的 ``onEvent`` 对齐（``kind`` 为帧类型标识）。

断线/关闭是正常生命周期，捕获 ``WebSocketDisconnect`` 后安静退出（不吞异常：只处理
「对端已断开」这一确定语义）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from host.adapters.ports import AppContext

router = APIRouter(tags=["events"])

POLL_INTERVAL_S = 0.5
FRAME_KIND = "STATUS"


@router.websocket("/events")
async def events_stream(websocket: WebSocket) -> None:
    """推送状态迁移 / 故障 / 按键 / 动作进度。"""
    await websocket.accept()
    context: AppContext = websocket.app.state.context
    last: dict[str, Any] | None = None
    try:
        while True:
            frame = _frame(context)
            if frame != last:
                await websocket.send_json(frame)
                last = frame
            await asyncio.sleep(POLL_INTERVAL_S)
    except WebSocketDisconnect:
        return


def _frame(context: AppContext) -> dict[str, Any]:
    """把状态快照投影成前端可消费的事件帧。"""
    snap = context.status_snapshot()
    return {
        "kind": FRAME_KIND,
        "state": snap["state"],
        "fault": snap.get("fault"),
        "docked": snap["docked"],
        "estop_healthy": snap["estop_healthy"],
        "mcu_armed": snap["mcu_armed"],
        "network_online": snap["network_online"],
        "role_id": snap["role_id"],
        "session_generation": snap["session_generation"],
        "action": snap.get("action"),
    }
