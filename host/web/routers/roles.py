"""角色路由：``GET /roles`` 列表、``PATCH /roles/active`` 切换、人设编辑。

切换即 ``session_generation += 1``（AC-15）；当前状态不允许切换时由
``AppContext.switch_active_role`` 抛 ``ConflictError`` → HTTP 409。

人设编辑分两个端点，**错误语义刻意不同**：

- ``PATCH /roles/{role_id}/persona``（保存）：内容非法 → 422。保存是写操作，
  非法内容必须让调用方明确失败，不能「收下了但其实没存」。
- ``POST /roles/{role_id}/persona/preview``（预览）：内容非法 → **200** +
  ``valid: false`` + 逐字段 ``errors``。UI 边打字边预览，要的是内联红字；
  返回 422 会被 fetch 层当成请求失败弹窗，把「标点不合规」升级成「系统故障」。
- 两者的 ``404`` 都只用于未知 role_id：那是资源不存在，不是内容问题。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from host.ai.persona import DEFAULT_PERSONA_MAX_CHARS, compile_persona
from host.core.errors import RecordNotFound
from host.roles.schema import PersonaConfig
from host.web import schemas
from host.web.deps import ContextDep

router = APIRouter(tags=["roles"])


@router.get("/roles")
def list_roles(ctx: ContextDep) -> dict[str, Any]:
    """可用角色及配置版本（含当前人设）。"""
    items = [schemas.RoleSummary(**s).model_dump() for s in ctx.role_summaries()]
    return schemas.ok(items)


@router.patch("/roles/active")
def set_active_role(payload: schemas.ActiveRolePatch, ctx: ContextDep) -> dict[str, Any]:
    """切换当前角色；正在运动/录音时返回 409。"""
    return schemas.ok(ctx.switch_active_role(payload.role_id))


def _validate_persona(payload: schemas.PersonaOut) -> PersonaConfig:
    """web DTO → 领域模型：**唯一校验点**（保存与预览共用，保证两边判定不分叉）。

    失败抛 pydantic 的 ``ValidationError``，由调用方决定是转 422（保存）还是
    转成 200 + errors（预览）。
    """
    return PersonaConfig.model_validate(payload.model_dump())


def _is_blank(persona: PersonaConfig) -> bool:
    """6 个字段全空（``max_chars`` 等于默认 100）→ 语义是「清空人设」。

    为什么含 ``max_chars == 100``：领域模型里 100 就是默认值，用户没动过它。
    若不判它，「全空但没改字数」会被当成一条真的人设写盘（编译出
    ``你是小星。每次不超过 100 字。`` 这种残句），``GET /roles`` 也会返回一个
    看起来有配置的 ``persona`` 对象 —— 与「没填」无法区分。
    代价（登记误伤面）：用户**只**想把人设字数调成 100 而不填任何其他字段时，
    会被判成清空。这没有实际损失：清空后同样不给字数限制、编译结果一致。
    """
    return (
        not persona.archetype
        and not persona.tone
        and not persona.speech_style
        and not persona.catchphrases
        and not persona.avoid
        and persona.max_chars == DEFAULT_PERSONA_MAX_CHARS
    )


def _require_role(role_id: int, ctx: ContextDep) -> None:
    if not ctx.roles.exists(role_id):
        raise RecordNotFound(f"未注册的角色: {role_id}")


@router.patch("/roles/{role_id}/persona")
def save_role_persona(
    role_id: int, payload: schemas.PersonaOut, ctx: ContextDep
) -> dict[str, Any]:
    """保存该角色的人设（写回 ``role.json``），返回落盘值与编译预览。

    **不 bump ``session_generation``**：正在进行的这一轮回复应该在**旧人设**下说完。
    bump 会让这一轮的回包因代数不匹配被丢弃，等于静默吞掉用户正等着的答案 ——
    比「这一轮还用旧人设」更糟。下一次对话自然用新人设（provider 每轮现取，见
    ``runtime._build_ai`` 的 ``persona_provider``），不需要在这里造一次切换。
    """
    _require_role(role_id, ctx)
    try:
        persona: PersonaConfig | None = _validate_persona(payload)
    except ValidationError as exc:
        # 复用 FastAPI 原生 422 形状（42201 + 逐字段 ``字段: 原因``），
        # 前端已按这个形状做定位；这里只负责把领域校验结果接进去。
        raise RequestValidationError(exc.errors()) from exc

    if persona is not None and _is_blank(persona):
        persona = None
    config = ctx.roles.save_persona(role_id, persona)
    saved = None if config.persona is None else schemas.PersonaOut(**config.persona.model_dump())
    result = schemas.PersonaSaveResult(
        role_id=role_id,
        persona=saved,
        persona_preview=compile_persona(config.name, config.persona),
    )
    return schemas.ok(result.model_dump())


@router.post("/roles/{role_id}/persona/preview")
def preview_role_persona(
    role_id: int, payload: schemas.PersonaOut, ctx: ContextDep
) -> dict[str, Any]:
    """编译预览：不写盘、永远 200（内容非法时给逐字段 errors）。"""
    _require_role(role_id, ctx)
    try:
        persona: PersonaConfig | None = _validate_persona(payload)
    except ValidationError as exc:
        errors = [
            {
                "path": ".".join(str(part) for part in err.get("loc", ())),
                "message": str(err.get("msg", "非法取值")),
            }
            for err in exc.errors()
        ]
        result = schemas.PersonaPreviewResult(valid=False, errors=errors, compiled=None)
        return schemas.ok(result.model_dump())

    if persona is not None and _is_blank(persona):
        persona = None  # 与保存路径同一判定：预览必须如实反映「存下去会是什么」
    config = ctx.roles.get(role_id)
    result = schemas.PersonaPreviewResult(
        valid=True,
        errors=[],
        compiled=compile_persona(config.name, persona),
    )
    return schemas.ok(result.model_dump())
