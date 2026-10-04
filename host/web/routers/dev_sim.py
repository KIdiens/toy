"""开发档位专用路由：**仅在 SIM 档位挂载**（``app.create_app`` 只在 ``simulate`` 时注册）。

用于无硬件阶段注入物理事件（拔出 / 按键 / 故障 / 解除）与手动排空队列，
使整条链路可在无设备环境端到端验收。生产档位不注册本路由；即便被误挂，
每个处理器也会再校验 ``ctx.simulate`` 并返回 404（纵深防御）。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from host.adapters.ports import AppContext
from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["dev"])

FaultCode = Literal["INTERLOCK", "TIMEOUT", "PWM_INIT", "SAFETY_TRIP", "PROVIDER", "STORAGE"]
SIM_ONLY_DETAIL = "仅 SIM 档位提供开发注入端点"


class ButtonRequest(BaseModel):
    request_id: str = Field(min_length=1)
    n: int = Field(ge=1, le=3)


class FaultRequest(BaseModel):
    request_id: str = Field(min_length=1)
    code: FaultCode = "SAFETY_TRIP"
    detail: str = Field(default="", max_length=120)


def _ensure_sim(ctx: AppContext) -> None:
    if not ctx.simulate:
        raise HTTPException(status_code=404, detail=SIM_ONLY_DETAIL)


@router.post("/dev/undock")
def dev_undock(ctx: ContextDep) -> dict[str, Any]:
    """模拟公仔被拔出（位信号丢失）。"""
    _ensure_sim(ctx)
    return schemas.ok(ctx.inject_undock())


@router.post("/dev/dock")
def dev_dock(ctx: ContextDep) -> dict[str, Any]:
    """模拟公仔重新插到位 / 急停复位。

    只恢复硬件前置条件，**不解除故障锁定**——仍需调用 ``clear_fault`` 完成确认。
    与 ``/dev/undock`` 成对，避免「拔出后忘记复位」导致无法自救。
    """
    _ensure_sim(ctx)
    return schemas.ok(ctx.inject_dock())


@router.post("/dev/button")
def dev_button(payload: ButtonRequest, ctx: ContextDep) -> dict[str, Any]:
    """注入一次按键（1=对话 / 2=表演 / 3=音量）。"""
    _ensure_sim(ctx)
    return schemas.ok(ctx.press_button(payload.n))


@router.post("/dev/fault")
def dev_fault(payload: FaultRequest, ctx: ContextDep) -> dict[str, Any]:
    """注入一次故障（抢占式停机）。"""
    _ensure_sim(ctx)
    return schemas.ok(ctx.trip_safety(payload.code, payload.detail or "开发档位注入的故障"))


@router.post("/dev/clear")
def dev_clear(ctx: ContextDep) -> dict[str, Any]:
    """人工确认解除故障锁定。"""
    _ensure_sim(ctx)
    return schemas.ok(ctx.clear_fault())


@router.post("/dev/pump")
def dev_pump(ctx: ContextDep) -> dict[str, Any]:
    """手动排空队列并返回最新状态（测试/调试用）。"""
    _ensure_sim(ctx)
    ctx.drain()
    return schemas.ok(ctx.status_snapshot())
