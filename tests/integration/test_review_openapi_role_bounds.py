"""openapi.yaml 的 ``role_id`` 边界必须与 ``web/schemas.py`` 逐字段对齐（组 5）。

## 缺陷

``host/web/schemas.py`` 里两个请求模型的 ``role_id`` 都是 ``Field(ge=1, le=3)``
（``ActiveRolePatch:33``、``ActionPlayRequest:43``），但 ``docs/api/openapi.yaml``
只写了一半：

- ``:90``（``PATCH /roles/active`` 内联 schema）只有 ``minimum: 1``，**缺上界**
- ``:488-490``（``components/schemas/ActionPlayRequest``）**连下界都没有**

后果：文档比实现宽松。前端/第三方按文档以为 ``role_id=7`` 合法而发过来，
拿到 422；文档生成出来的客户端类型是 ``number``，静态检查全过，
问题被推迟到联调现场。

同文件 ``:513``（``ActionDoc.role_id``）**双边界齐全** —— 说明这不是
「没人想过」，而是没有逐字段对齐的机制。

## 为什么响应侧**不能**加边界（本文件最重要的一条）

第一版打算把 ``Status.role_id`` / ``RoleSummary.role_id`` 也补上
``minimum: 1, maximum: 3``。实测证明那是**错的**：

``host/adapters/ports.py:106`` 是 ``self.roles.active_role_id or 0`` ——
未装公仔时 ``active_role_id`` 为 ``None``，经 ``or 0`` 变成 ``0``。
实测（把 ``_active`` 置None 后打 ``GET /api/v1/status``）确实返回
``role_id: 0``。

响应侧补下界会让文档**与实现相反**：真机未装公仔时前端看到 0，
按文档这是非法值。这是纯粹的「为了让文档好看而制造漂移」。

代码侧也没给响应模型加边界（``Status.role_id`` / ``RoleSummary.role_id``
都是裸 ``int``），文档与代码在响应侧本来就一致 —— 不动。

## 定位方式：不能硬编码行号

本文件第一版用「邻近文本锚点 + 固定 ±400 字符窗口」定位，两条用例全红——
不是产品缺陷，是**我的定位逻辑弱**：

- ``minLength: 1`` 在文件里出现多次，窗口捞到了别的 schema
- ``:90`` 是**内联在 path 下**的 schema，压根不在 ``components/schemas`` 段里

改为：``components/schemas`` 下的按 schema 名截块；内联的那处按
``operationId`` 截块。两种定位方式不同，用同一个函数会漏。
"""
from __future__ import annotations

import re

import pytest

from tests.support import TOY_ROOT

OPENAPI_PATH = TOY_ROOT / "docs" / "api" / "openapi.yaml"
INLINE_ROLE = re.compile(r"role_id:\s*\{(?P<body>[^}]*)\}")


def _read() -> str:
    return OPENAPI_PATH.read_text(encoding="utf-8")


def _component_block(name: str) -> str:
    """截取 ``components/schemas/<name>`` 到下一个同级 schema 名之间的内容。"""
    text = _read()
    if f"\n    {name}:\n" not in text:
        pytest.fail(
            f"openapi.yaml 里找不到 components/schemas/{name}。"
            "若该 schema 已改名/删除，请同步更新本测试；"
            "若只是挪了位置，那是本测试的定位方式需要改。"
        )
    start = text.index(f"\n    {name}:\n")
    lines = text[start + 1:].splitlines()
    collected: list[str] = []
    for line in lines[1:]:
        if re.match(r"^    [A-Za-z_][A-Za-z0-9_]*:\s*$", line):
            break
        collected.append(line)
    assert collected, f"openapi.yaml 里找不到 components/schemas/{name}"
    return "\n".join(collected)


def _operation_block(operation_id: str) -> str:
    """截取某个 operation（按 ``operationId`` 定位）到下一个同级 operation 为止。

    定位失败时抛**带锚点名字**的断言，而不是裸 ``ValueError`` ——
    否则报错只说 ``substring not found``，读的人得自己去猜是哪个 operationId。
    """
    text = _read()
    anchor = f"operationId: {operation_id}\n"
    if anchor not in text:
        pytest.fail(
            f"openapi.yaml 里找不到 operationId={operation_id}。"
            "若该接口已改名/ 删除，请同步更新本测试的 REQUEST_SITES；"
            "若只是路径挪了位置，那是本测试的定位方式需要改。"
        )
    start = text.index(anchor)
    lines = text[start:].splitlines()
    collected: list[str] = []
    for line in lines[1:]:
        #下一个 operation 以6 空格缩进的动词开头（get/post/patch/...）
        if re.match(r"^ {6}(get|post|put|patch|delete|options|head):\s*$", line):
            break
        collected.append(line)
    assert collected, f"openapi.yaml 里找不到 operationId={operation_id}"
    return "\n".join(collected)


def _role_id_body(block: str) -> str:
    """从一段 YAML 里取出 ``role_id`` 的定义（内联或块状），归一化成单行。"""
    match = INLINE_ROLE.search(block)
    if match:
        return " ".join(match.group("body").split())
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "role_id:":
            continue
        indent = len(line) - len(line.lstrip())
        chunk: list[str] = []
        for follow in lines[i + 1:]:
            if follow.strip() and (len(follow) - len(follow.lstrip())) <= indent:
                break
            chunk.append(follow.strip())
        return " ".join(chunk)
    raise AssertionError("这段 YAML 里没有 role_id 定义")


# =====================================================================
# 1. 请求侧两处必须双边界齐全
# =====================================================================
#: ``(标签, 取块方式, 取块参数)`` —— 两处的定位方式**不同**，故不能合并成一个参数
REQUEST_SITES = [
    ("setActiveRole", _operation_block, "setActiveRole"),   # 内联 schema（:87-90）
    ("ActionPlayRequest", _component_block, "ActionPlayRequest"),  # 引用 schema（:483）
]


@pytest.mark.parametrize("label,getter,target", REQUEST_SITES)
def test_request_side_role_id_has_both_bounds(
        label: str, getter, target: str) -> None:
    """两处请求模型的 ``role_id`` 都必须 ``minimum: 1`` 且 ``maximum: 3``。

    代码真源：``web/schemas.py:33`` 与 ``:43`` 都是 ``Field(ge=1, le=3)``。
    """
    body = _role_id_body(getter(target))
    assert "type: integer" in body, f"{label}.role_id 未声明 integer：{body}"
    assert "minimum: 1" in body, f"{label}.role_id 缺下界：{body}"
    assert "maximum: 3" in body, f"{label}.role_id 缺上界：{body}"


def test_both_request_sites_are_covered_not_just_one() -> None:
    """两处都要查，且要能报出**是哪一处**缺。

    只查其中一处然后就算过，是没有判别力的写法：改对 ``:90`` 忘改
    ``:488`` 时测试仍绿。这里独立复核一遍，并让失败信息带上位置。
    """
    missing = {}
    for label, getter, target in REQUEST_SITES:
        body = _role_id_body(getter(target))
        if "minimum: 1" not in body or "maximum: 3" not in body:
            missing[label] = body
    assert not missing, f"这些请求侧 role_id 边界不全：{missing}"


# =====================================================================
# 2. 响应侧不得有边界（未装公仔时 role_id 就是 0）
# =====================================================================
@pytest.mark.parametrize("schema_name", ["Status", "RoleSummary"])
def test_response_side_role_id_has_no_bounds(schema_name: str) -> None:
    """响应侧 ``role_id`` 不该有边界，尤其**不能有下界**。

    实现依据：``ports.py:106`` 的 ``self.roles.active_role_id or 0``。
    实测：``_active`` 为 None 时 ``GET /api/v1/status`` 返回 ``role_id: 0``。
    加了 ``minimum: 1`` 会让文档与实现相反。

    上界同样不加：角色编号集合可能随内容包扩展，响应侧写死边界会
    在扩角色时静默失效。
    """
    body = _role_id_body(_component_block(schema_name))
    assert "minimum: 1" not in body, f"{schema_name}.role_id 不能有下界：{body}"
    assert "maximum: 3" not in body, f"{schema_name}.role_id 不能有上界：{body}"


# =====================================================================
# 3. 与代码真源的绑定（代码改了文档必须跟着重估）
# =====================================================================
def test_request_models_in_code_really_have_both_bounds() -> None:
    """代码侧必须是**恰好** ``ge=1, le=3``，与文档声称的边界逐值对齐。

    这是「文档声称的边界」与「实现真正强制的边界」的对照点。

    **不能只判``field.metadata`` 非空**（本文件第一版就是这么写的，假绿）：
    那样只能抓住「约束被整个删掉」，抓不住「约束被改错」——
    实测把 ``le=3`` 改成 ``le=9``（文档仍写 ``maximum: 3``）时全绿通过。
    边界漂移必须是**值相等**才算对齐。

    若实现真的放宽成不校验，或收窄成别的区间，本条转红，
    提醒文档那侧的断言需要重新评估。
    """
    from annotated_types import Ge, Le
    from host.web import schemas

    for model in (schemas.ActiveRolePatch, schemas.ActionPlayRequest):
        field = model.model_fields["role_id"]
        lower = [m.ge for m in field.metadata if isinstance(m, Ge)]
        upper = [m.le for m in field.metadata if isinstance(m, Le)]
        assert lower == [1], f"{model.__name__}.role_id 下界是 {lower}，文档声称 minimum: 1"
        assert upper == [3], f"{model.__name__}.role_id 上界是 {upper}，文档声称 maximum: 3"


def test_code_boundaries_are_not_wider_than_documented_ones() -> None:
    """反向约束：实现**不得比文档更宽**，否则文档就是在骗客户端。

    这是上面那条的加强版：即使有人把边界改成不对称的值
    （比如 ``ge=1`` 但去掉上界），文档侧的 ``maximum: 3`` 也失去了约束力。
    """
    from annotated_types import Ge, Le
    from host.web import schemas

    for model in (schemas.ActiveRolePatch, schemas.ActionPlayRequest):
        field = model.model_fields["role_id"]
        assert any(isinstance(m, Ge) for m in field.metadata), (
            f"{model.__name__}.role_id 没有下界，文档却写着 minimum: 1"
        )
        assert any(isinstance(m, Le) for m in field.metadata), (
            f"{model.__name__}.role_id 没有上界，文档却写着 maximum: 3"
        )


def test_response_models_in_code_really_have_no_bounds() -> None:
    """``Status`` / ``RoleSummary`` 在代码里就是裸 int，与文档一致。

    若有人给它们加了 ``ge=1``，本条会红 —— 那时要先回答：
    未装公仔返回 0 怎么办？（答案应该是「改用可空字段」，不是收紧边界。）
    """
    from host.web import schemas

    for model in (schemas.Status, schemas.RoleSummary):
        field = model.model_fields["role_id"]
        assert not field.metadata, (
            f"{model.__name__}.role_id 在代码里加了边界，"
            "但 role_id=0 是合法的未装公仔状态 —— 先确认是不是真要收紧"
        )


def test_status_role_id_zero_is_reachable_at_runtime() -> None:
    """实测支撑：``role_id=0`` 真的能从接口返回（不只是读代码推断）。

    这条是第 2 节断言的**行为级判别力来源**：若哪天``ports.py:106``
    改成不返回 0（例如改成可空字段），本条会红，提醒文档可以收紧了。
    """
    import inspect

    from host.adapters import ports

    source = inspect.getsource(ports)
    assert "active_role_id or 0" in source, (
        "ports.py 里Status.role_id 的 0 兜底被改了 —— "
        "响应侧文档能否收紧需要重新评估"
    )