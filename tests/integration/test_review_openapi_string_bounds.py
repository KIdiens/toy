"""openapi.yaml 的字符串长度边界 / nullable 必须与 ``web/schemas.py`` 对齐（P2）。

## 缺陷（与组 5 同类，组 5 只修了 2/8）

组 5 补齐了 ``role_id`` 的数值边界，但**字符串长度边界全数漂移**：

| 模型.字段 | 代码真源 | 文档 | 状态 |
|---|---|---|---|
| ActionPlayRequest.request_id | ``min_length=1`` | 无 |缺 |
| ActionPlayRequest.action_id | ``min_length=1, max_length=60`` | 无 | 缺 |
| ReminderCreateRequest.request_id | ``min_length=1`` | 无 | 缺 |
| ReminderCreateRequest.profile_id | ``min_length=1`` | 无 | 缺 |
| ReminderCreateRequest.text | ``min_length=1, max_length=200`` | 只有 ``maxLength: 200`` |
| ReminderCreateRequest.due_at_utc | ``min_length=1`` | 无 | 缺 |
| ReminderCreateRequest.idempotency_key | ``max_length=64`` | 无 | 缺 |

有**真实后果**（实测）：发一个 80 字符的 ``action_id``，接口返回 422
``String should have at most 60 characters``。这正是组 5 自己论证过的
「文档比实现宽松 → 生成客户端静态检查全过 → 问题推迟到联调现场」。

## 顺带发现：idempotency_key 少了 nullable

``idempotency_key: str | None = Field(default=None, ...)`` 是可选字段，
但文档只写了 ``{ type: string }``，**没标 ``nullable: true``**——
而同schema 里的 ``repeat_rule`` 标了。按文档生成的客户端会把它当必填字符串。

## 为什么这组能写成「数据驱动」而不是逐条硬编码

下面用 ``_EXPECTED`` 显式列出期望，让「漏了哪一条」在失败信息里一目了然；
但**真源仍是 ``host/web/schemas.py`` 的 ``model_fields``** ——
``test_documented_bounds_match_the_code_models`` 直接从 pydantic 读约束
再比对文档，不靠人工誊抄。誊抄的版本必然漂移，那正是本缺陷的成因。
"""
from __future__ import annotations

import re

import pytest

from tests.support import TOY_ROOT

OPENAPI_PATH = TOY_ROOT / "docs" / "api" / "openapi.yaml"
INLINE_FIELD = re.compile(r"^\s*(?P<name>\w+):\s*\{(?P<body>[^}]*)\}\s*$")


def _read() -> str:
    return OPENAPI_PATH.read_text(encoding="utf-8")


def _component_block(name: str) -> str:
    text = _read()
    marker = f"\n    {name}:\n"
    if marker not in text:
        pytest.fail(f"openapi.yaml 里找不到 components/schemas/{name}")
    # 从 marker **整行之后**开始取（不是 +1 —— marker 以换行结尾，
    # +1 只跳过那1 个换行符，splitlines() 的第 0 行又变回 schema 名本身，
    # 会被下面的「下一个同级 schema」正则立刻命中而 break，块就成了空的）。
    lines = text[text.index(marker) + len(marker):].splitlines()
    collected: list[str] = []
    for line in lines:
        if re.match(r"^    [A-Za-z_][A-Za-z0-9_]*:\s*$", line):
            break
        collected.append(line)
    return "\n".join(collected)


def _operation_block(operation_id: str) -> str:
    text = _read()
    anchor = f"operationId: {operation_id}\n"
    if anchor not in text:
        pytest.fail(
            f"openapi.yaml 里找不到 operationId={operation_id}。"
            "若接口改名/删除了，请同步更新本测试；若只是挪了位置，是定位方式要改。"
        )
    # 按**位置**切，不能写 text[anchor:] —— 那是把字符串当切片下标，
    # 直接 TypeError: slice indices must be integers。
    collected: list[str] = []
    for line in text[text.index(anchor):].splitlines()[1:]:
        if re.match(r"^ {6}(get|post|put|patch|delete|options|head):\s*$", line):
            break
        collected.append(line)
    return "\n".join(collected)


def _field_body(block: str, field: str) -> str:
    """取某个字段的定义（内联或块状），归一化成单行。"""
    for line in block.splitlines():
        match = INLINE_FIELD.match(line)
        if match and match.group("name") == field:
            return " ".join(match.group("body").split())
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != f"{field}:":
            continue
        indent = len(line) - len(line.lstrip())
        chunk: list[str] = []
        for follow in lines[i + 1:]:
            if follow.strip() and (len(follow) - len(follow.lstrip())) <= indent:
                break
            chunk.append(follow.strip())
        return " ".join(chunk)
    raise AssertionError(f"这段 YAML 里没有 {field} 字段")


# =====================================================================
# 1. 显式期望表（漏了哪一条在失败信息里可见）
# =====================================================================
#: ``(标签, 取块方式, 块参数, 字段, (期望片段, ...))``
#:
#: 期望片段必须打包成元组 —— pytest 的 parametrize **不支持** ``*args`` 解包，
#: 本文件第一版写成 ``(getter, target, field, *expected)``，收集期直接报
#: ``the number of names (4) must be equal to the number of values (5)``。
EXPECTED_SITES = [
    # PATCH /roles/active（内联 schema）
    ("setActiveRole.request_id", _operation_block, "setActiveRole",
     "request_id", ("minLength: 1",)),
    # components/schemas 里的
    ("ActionPlayRequest.request_id", _component_block, "ActionPlayRequest",
     "request_id", ("minLength: 1",)),
    ("ActionPlayRequest.action_id", _component_block, "ActionPlayRequest",
     "action_id", ("minLength: 1", "maxLength: 60")),
    ("ReminderCreateRequest.request_id", _component_block, "ReminderCreateRequest",
     "request_id", ("minLength: 1",)),
    ("ReminderCreateRequest.profile_id", _component_block, "ReminderCreateRequest",
     "profile_id", ("minLength: 1",)),
    ("ReminderCreateRequest.text", _component_block, "ReminderCreateRequest",
     "text", ("minLength: 1", "maxLength: 200")),
    ("ReminderCreateRequest.due_at_utc", _component_block, "ReminderCreateRequest",
     "due_at_utc", ("minLength: 1",)),
    ("ReminderCreateRequest.idempotency_key", _component_block,
     "ReminderCreateRequest", "idempotency_key", ("maxLength: 64",)),
]


@pytest.mark.parametrize("label,getter,target,field,expected", EXPECTED_SITES)
def test_documented_string_bounds(label, getter, target: str, field: str,
                                  expected: tuple[str, ...]) -> None:
    """每个字符串字段的长度约束必须在文档里齐全。"""
    body = _field_body(getter(target), field)
    assert "type: string" in body, f"{label} 未声明 string：{body}"
    for fragment in expected:
        assert fragment in body, f"{label} 缺 {fragment}：{body}"


def test_every_expected_site_is_actually_checked() -> None:
    """防空集恒绿 / 漏测：表里的每一条都必须真的被测到。"""
    assert len(EXPECTED_SITES) == 8, EXPECTED_SITES
    for label, getter, target, field, expected in EXPECTED_SITES:
        body = _field_body(getter(target), field)
        for fragment in expected:
            assert fragment in body, (
                f"{label} 现在就缺 {fragment} —— "
                "若这是预期的「已知未修」，请更新 EXPECTED_SITES 而不是放着红"
            )


# =====================================================================
# 2. 与代码真源对照（不靠人工誊抄——誊抄必然漂移，那正是本缺陷成因）
# =====================================================================
def _code_string_bounds(model) -> dict[str, tuple[int | None, int | None]]:
    """取**字符串长度**约束。

    **必须用 ``MinLen`` / ``MaxLen``，不能用 ``Ge`` / ``Le``** —— pydantic 对
    ``Field(min_length=1)`` 产出的是 ``annotated_types.MinLen``；而 ``Ge``/``Le``
    是给数值字段（``role_id: int = Field(ge=1, le=3)``）用的。

    本文件第一版误用 ``Ge``/``Le``，结果把 ``role_id`` 的**数值**边界
    当成字符串长度去文档里找 ``minLength: 1`` —— 报出
    ``ActionPlayRequest 的文档缺少长度约束：{'role_id': [...]}``，
    看着像文档缺 ``role_id`` 的长度约束，实际是找错了关键字。
    数值边界的职责属``test_review_openapi_role_bounds.py``，此处不越界管。
    """
    from annotated_types import MaxLen, MinLen

    out: dict[str, tuple[int | None, int | None]] = {}
    for name, field in model.model_fields.items():
        if field.annotation is not str:
            continue  # 只管 str 字段；``str | None`` 的在nullable 那节管
        lower = next((m.min_length for m in field.metadata if isinstance(m, MinLen)), None)
        upper = next((m.max_length for m in field.metadata if isinstance(m, MaxLen)), None)
        out[name] = (lower, upper)
    return out


@pytest.mark.parametrize("model_name", ["ActionPlayRequest", "ReminderCreateRequest"])
def test_documented_bounds_match_the_code_models(model_name: str) -> None:
    """代码里每个字符串字段的长度约束，都必须能在文档里找到。

    真源是 pydantic 的 ``model_fields``，不是人工誊抄的期望表——
    这样将来代码加了约束而文档没跟，本条会直接报出字段名。
    """
    from host.web import schemas

    model = getattr(schemas, model_name)
    block = _component_block(model_name)
    missing: dict[str, list[str]] = {}

    for field_name, (lower, upper) in _code_string_bounds(model).items():
        if lower is None and upper is None:
            continue
        try:
            body = _field_body(block, field_name)
        except AssertionError:
            missing[field_name] = ["文档里根本没有这个字段"]
            continue
        want: list[str] = []
        if lower is not None:
            want.append(f"minLength: {lower}")
        if upper is not None:
            want.append(f"maxLength: {upper}")
        gaps = [w for w in want if w not in body]
        if gaps:
            missing[field_name] = gaps

    assert not missing, f"{model_name} 的文档缺少长度约束：{missing}"


def test_string_bounds_helper_actually_finds_the_known_constraints() -> None:
    """防空集恒绿：辅助函数必须真的能从代码里读出约束。

    没有判别力的写法是「helper 返回空 dict → 循环体不执行 → 断言通过」。
    钉住它确实读到了 ``action_id`` 的 ``(1, 60)``。
    """
    from host.web import schemas

    bounds = _code_string_bounds(schemas.ActionPlayRequest)
    assert bounds.get("action_id") == (1, 60), bounds
    assert bounds.get("request_id") == (1, None), bounds
    # 数值字段不在本helper 的职责内
    assert "role_id" not in bounds, bounds


# =====================================================================
# 3. nullable 必须与 str | None 对齐
# =====================================================================
@pytest.mark.parametrize("field", ["repeat_rule", "idempotency_key"])
def test_optional_fields_are_marked_nullable(field: str) -> None:
    """``str | None`` 的字段必须标 ``nullable: true``。

    ``repeat_rule`` 本来就标了，``idempotency_key`` 没标 ——
    按文档生成的客户端会把可选字段当必填字符串。
    """
    body = _field_body(_component_block("ReminderCreateRequest"), field)
    assert "nullable: true" in body, f"idempotency_key 之类的可选字段缺 nullable：{body}"


def test_optional_fields_in_code_really_are_optional() -> None:
    """代码侧的 Optional 判定，防止文档标了 nullable 而代码其实是必填。"""
    from host.web import schemas

    for field in ("repeat_rule", "idempotency_key"):
        assert schemas.ReminderCreateRequest.model_fields[field].annotation == (
            str | None
        ), f"{field} 在代码里已不是可选，文档的 nullable 需要重新评估"


# =====================================================================
# 4. 行为级证据：文档没写的约束，接口真的会拒
# =====================================================================
def test_undocumented_length_limit_actually_returns_422() -> None:
    """实测：80 字符的 action_id 被拒，而文档对此**只字未提**。

    这是本缺陷「为什么要修」的行为级证据 —— 不是文档洁癖，
    而是照文档写的客户端会在联调现场收到 422。
    """
    from host.web import schemas
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as info:
        schemas.ActionPlayRequest(
            request_id="r1", role_id=1, action_id="x" * 80
        )
    assert "at most 60" in str(info.value), info.value

    # 边界内必须通过（防止把约束收得过紧，误伤合法输入）
    schemas.ActionPlayRequest(request_id="r1", role_id=1, action_id="x" * 60)


def test_undocumented_empty_string_rejection_is_real() -> None:
    """实测：空 ``request_id`` 被拒（``min_length=1``），文档同样没写。"""
    from host.web import schemas
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.ReminderCreateRequest(
            request_id="", profile_id="p", text="t", due_at_utc="2030-01-01T00:00:00Z"
        )
    # 合法的最小值必须通过
    schemas.ReminderCreateRequest(
        request_id="r", profile_id="p", text="t", due_at_utc="2030-01-01T00:00:00Z"
    )