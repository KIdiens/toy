"""工具 schema 必须是**合法 JSON Schema**（组2）。

## 缺陷背景

``host/ai/tools.py::build_spec`` 原来用 ``p.type.__name__`` 生成 ``type`` 字段，
产出 ``{"type": "str"}`` / ``{"type": "int"}``。而 JSON Schema（draft 2020-12，
OpenAI function-calling 沿用）只认 ``"string"`` / ``"integer"`` / ``"number"`` /
``"boolean"``。这份 schema 由 ``host/ai/qwen_provider.py:149`` **原样发给模型**，
所以修之前是**每一次真实工具调用都在发一份 type 非法的 schema**。

这不是 latent，是当时就在发生的。影响面也不止「不合法」—— 模型看到 ``"int"``
往往按字符串理解，生成 `"5"` 而不是 `5`，反过来触发本地类型拒绝，
让模型调用工具的成功率下降。

##顺带封掉的后门

``ParamSpec(str, minimum=1.0)`` 这种组合在 ``validate_args`` 里会执行
``"北京" < 1.0`` → ``TypeError``。它**不是** ``ToolValidationError``，
会逃过 ``host/ai/router.py:128`` 只捕获 ``ToolValidationError`` 的白名单拒绝路径，
把「参数非法」升级成「整轮对话失败」。当前 8 处``ParamSpec`` 恰好没有这种组合
（已逐个核对），所以本组把它作为**构造期守卫**封掉，而不是运行时补丁。
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass

import pytest
from host.ai.providers import ToolSpec
from host.ai.tools import (
    JSON_TYPE_NAMES,
    NUMERIC_TYPES,
    ParamSpec,
    ToolRegistry,
    build_spec,
)
from host.core.errors import ToolValidationError

from tests.support import TOY_ROOT

# JSON Schema draft 2020-12 的合法 type 关键字全集（本项目只会用到前四个）。
VALID_JSON_TYPES = {
    "string", "integer", "number", "boolean", "array", "object", "null",
}


# =====================================================================
# 1. schema 的type 必须是规范关键字
# =====================================================================
@pytest.mark.parametrize("py_type", sorted(JSON_TYPE_NAMES, key=lambda t: t.__name__))
def test_json_type_names_cover_every_supported_python_type(py_type: type) -> None:
    # 讽刺但真实：写这条测试时我先用了 ``sorted(JSON_TYPE_NAMES.items())``，
    # 立刻撞上「type 不可比较」的 TypeError —— 正是本组在修的那类错误。
    json_name = JSON_TYPE_NAMES[py_type]
    assert json_name in VALID_JSON_TYPES, f"{json_name} 不是合法 JSON Schema type"
    spec = build_spec("t", "d", (ParamSpec("p", py_type),))
    got = spec.parameters["properties"]["p"]["type"]
    assert got == json_name, f"{py_type.__name__} 被发成 {got!r}，应为 {json_name!r}"


def test_no_python_type_name_leaks_into_the_schema() -> None:
    """**缺陷的正面对照**：任何 Python 类型名都不得出现在 schema 的 ``type`` 字段。

    这条比逐类型断言更强：将来有人加一种新类型而忘了登记 ``JSON_TYPE_NAMES``，
    构造期就会拒（见下一条），不会漏到这里。
    """
    params = (
        ParamSpec("s", str), ParamSpec("i", int),
        ParamSpec("f", float), ParamSpec("b", bool),
    )
    properties = build_spec("t", "d", params).parameters["properties"]
    for name, schema in properties.items():
        assert schema["type"] not in {"str", "int", "float", "bool"}, (
            f"参数 {name} 的 schema 里出现了 Python 类型名：{schema}"
        )


def test_schema_is_json_serialisable_and_wellformed() -> None:
    """整份 schema 必须能 ``json.dumps`` 且结构符合 object schema 形状。

    真实provider 会把它塞进 JSON 请求体，所以「能序列化」是最低要求。
    """
    spec = build_spec("get_weather", "查天气", (
        ParamSpec("city", str, required=True, pattern=r".+"),
        ParamSpec("days", int, required=False, minimum=1, maximum=5),
    ))
    text = json.dumps(spec.parameters, ensure_ascii=False)
    restored = json.loads(text)
    assert restored["type"] == "object"
    assert set(restored) == {"type", "properties", "required"}
    assert restored["required"] == ["city"]
    assert restored["properties"]["days"] == {
        "type": "integer", "minimum": 1, "maximum": 5,
    }


def test_bool_param_does_not_leak_as_integer() -> None:
    """``bool`` 是 ``int`` 的子类，最容易在映射表里被合并掉 —— 单独钉死。"""
    spec = build_spec("t", "d", (ParamSpec("flag", bool),))
    assert spec.parameters["properties"]["flag"]["type"] == "boolean"


# =====================================================================
# 2. 构造期守卫：非法 ParamSpec 直接拒（而不是留到运行时炸）
# =====================================================================
@pytest.mark.parametrize("bad_type", [list, dict, tuple, set, object])
def test_unsupported_param_type_is_rejected_at_construction(bad_type: type) -> None:
    """没有登记 JSON 关键字的类型必须在**构造时**拒。

    不拒的话它会一路走到 ``build_spec``，而那里用 ``JSON_TYPE_NAMES[p.type]``
    会抛 ``KeyError`` —— 报错点离根因很远，且只在真实调用时才暴露。
    """
    with pytest.raises(ValueError, match="JSON Schema"):
        ParamSpec("p", bad_type)


@pytest.mark.parametrize("py_type", [str, bool])
def test_non_numeric_type_cannot_carry_numeric_bounds(py_type: type) -> None:
    """字符串/布尔配``minimum``/``maximum`` 必须在构造时拒。

    这正是会抛 ``TypeError`` 的组合；``TypeError`` 逃出 ``ToolValidationError``
    白名单后，整轮对话会因一个「参数填错」而失败。
    """
    with pytest.raises(ValueError, match="不能配数值边界"):
        ParamSpec("p", py_type, minimum=1.0)
    with pytest.raises(ValueError, match="不能配数值边界"):
        ParamSpec("p", py_type, maximum=1.0)


@pytest.mark.parametrize("py_type", sorted(NUMERIC_TYPES, key=lambda t: t.__name__))
def test_numeric_types_can_still_carry_bounds(py_type: type) -> None:
    """守卫不得误伤：数值类型配边界必须照常可用。"""
    spec = ParamSpec("p", py_type, minimum=0, maximum=10)
    assert spec.minimum == 0 and spec.maximum == 10


def test_string_param_with_pattern_still_works() -> None:
    """现有 8 处 ``ParamSpec`` 大量用``str + pattern``，守卫不能牵连它们。"""
    spec = ParamSpec("city", str, required=True, pattern=r"[A-Za-z]+")
    assert spec.pattern == r"[A-Za-z]+"
    assert JSON_TYPE_NAMES[spec.type] == "string"


# =====================================================================
# 3. 类型错误消息用规范名（排障时两边要对得上号）
# =====================================================================
def _registry_with_city() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "get_weather", "查天气",
        (ParamSpec("city", str, required=True),),
        lambda args: {"ok": True, "city": args["city"]},
    )
    return registry


def test_type_error_message_uses_json_schema_name() -> None:
    """错误消息要报 ``string`` 而不是 ``str``。

    调用方看到的是模型给的 arguments，模型看到的是 schema 里的 ``"string"``。
    消息里报 Python 名会让排障时两边对不上号。
    """
    with pytest.raises(ToolValidationError) as excinfo:
        _registry_with_city().validate_args("get_weather", {"city": 123})
    message = str(excinfo.value)
    assert "类型应为" in message and " string" in message, message
    # 精确比较而非子串：``"类型应为 str"`` 是 ``"类型应为 string"`` 的子串，
    # 用 ``not in`` 断言会把自己判红（写这条时踩过）。
    assert message == "参数 city 类型应为 string", message


def test_still_rejects_non_json_serialisable_values() -> None:
    """类型收紧不得放过容器/对象：``dict``/``list`` 仍必须被拒。"""
    for bad in ([], {}, object()):
        with pytest.raises(ToolValidationError):
            _registry_with_city().validate_args("get_weather", {"city": bad})


def test_bool_is_not_accepted_as_int() -> None:
    """``bool`` 是 ``int`` 子类：``days=True`` 必须被拒（否则 minimum 检查会放行）。"""
    registry = ToolRegistry()
    registry.register(
        "f", "d", (ParamSpec("days", int, required=True, minimum=1),),
        lambda args: {"ok": True},
    )
    with pytest.raises(ToolValidationError, match="integer"):
        registry.validate_args("f", {"days": True})


# =====================================================================
# 4. 真实注册表巡检：线上工具的 schema 必须全部合法
# =====================================================================
@dataclass(frozen=True)
class _DeclaredSpec:
    """一处 ``ParamSpec(...)`` 的**声明原文**（关键字参数全保留）。

    ``declared`` 是 ast 静态求值出来的原始关键字（``minimum`` / ``pattern`` /
    ``choices`` / ...），``spec`` 是用它们构造出来的真实 ``ParamSpec``。
    """

    label: str
    spec: ParamSpec
    declared: dict[str, object]


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """模块级字面量常量（``CITY_PATTERN = r"..."`` 这类），供 ``pattern=常量名`` 求值。"""
    constants: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            constants[target.id] = ast.literal_eval(node.value)
        except ValueError:
            continue
    return constants


_TYPE_BY_NAME = {"str": str, "int": int, "float": float, "bool": bool}


def _literal(node: ast.AST, constants: dict[str, object], *, where: str) -> object:
    """把实参节点求值成 Python 值；名字先查模块常量，其次查内置参数类型。"""
    if isinstance(node, ast.Name):
        if node.id in constants:
            return constants[node.id]
        if node.id in _TYPE_BY_NAME:
            return _TYPE_BY_NAME[node.id]   # 位置实参写的是内置类型 str/int/...
        raise AssertionError(f"{where}: 无法静态求值的名字 {node.id}")
    try:
        return ast.literal_eval(node)
    except ValueError as exc:  # pragma: no cover - 只有写成表达式才会到这里
        raise AssertionError(f"{where}: 参数不是字面量（{ast.unparse(node)}）") from exc


def _all_registered_specs() -> list[_DeclaredSpec]:
    """从 ``host/tools/*`` 收集所有实际注册用的 ``ParamSpec``（含全部约束值）。

    刻意用**源码静态扫描**而不是 import 真实 registry：那些 ``register()`` 需要
    client / db / 凭据，未必能裸 import；而本组要验的是「声明的规格」本身能否
    产出合法且不丢约束的 schema。

    **为什么必须用 ast**（改前是正则 ``ParamSpec\\(([^)]*)\\)``）：旧实现把捕获到的
    实参按逗号切开后**只重建了 name / type / required**，``minimum`` / ``maximum`` /
    ``pattern`` / ``choices`` 全部丢弃，于是「约束值」根本没进入被测对象 ——
    审计复现：把 ``reminders.py`` 的 ``ParamSpec("id", int, minimum=1)`` 改成
    ``minimum=-100``，改前的本文件 23 条全绿。ast 能拿到关键字实参的名字与字面量值，
    一个都不会漏；顺带也不会被值里出现的右括号（例如 ``pattern=r"[)]{1,60}"``）截断。
    """
    found: list[_DeclaredSpec] = []
    for path in sorted((TOY_ROOT / "host" / "tools").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ParamSpec"):
                continue
            where = f"{path.name}:{node.lineno}"
            positional = [_literal(arg, constants, where=where) for arg in node.args]
            kwargs = {kw.arg: _literal(kw.value, constants, where=where)
                      for kw in node.keywords if kw.arg is not None}
            if positional:
                kwargs["name"] = positional[0]
            if len(positional) > 1:
                kwargs["type"] = positional[1]
            type_name = kwargs.pop("type", "str")
            if isinstance(type_name, str):
                if type_name not in _TYPE_BY_NAME:
                    raise AssertionError(f"{where}: 未知参数类型 {type_name!r}")
                py_type = _TYPE_BY_NAME[type_name]
            else:
                py_type = type_name  # type: ignore[assignment]
            name = str(kwargs.pop("name"))
            spec = ParamSpec(name, py_type, **kwargs)
            declared = {"type": py_type, **kwargs}
            found.append(_DeclaredSpec(f"{path.name}:{name}", spec, declared))
    return found


_DECLARED_SPECS = _all_registered_specs()


def _json_type_of(declared: dict[str, object]) -> str:
    return JSON_TYPE_NAMES[declared["type"]]  # type: ignore[index]


def _expected_schema(declared: dict[str, object]) -> dict[str, object]:
    """**独立于 ``build_spec``** 地按 JSON Schema 语义写出「应该长什么样」。

    期望值只来自声明原文，不经过 ``build_spec`` —— 否则就成了「拿实现验实现」。
    （``type`` 名仍走 ``JSON_TYPE_NAMES``，那是另一件事：它自身由本文件上半部分
    的 ``test_json_type_names_cover_every_supported_python_type`` 单独钉住。）
    ``build_spec`` 丢弃或改写任一约束键，这里立刻对不上。
    """
    schema: dict[str, object] = {"type": _json_type_of(declared)}
    choices = declared.get("choices")
    if choices:
        schema["enum"] = list(choices)  # type: ignore[arg-type]
    if declared.get("minimum") is not None:
        schema["minimum"] = declared["minimum"]
    if declared.get("maximum") is not None:
        schema["maximum"] = declared["maximum"]
    return schema


def test_every_registered_param_spec_produces_a_valid_schema() -> None:
    """线上全部工具参数的 schema 必须合法 —— 这是「组2 已修好」的直接证据。"""
    assert len(_DECLARED_SPECS) >= 8, (
        f"只扫到 {len(_DECLARED_SPECS)} 处 ParamSpec，扫描规则可能失效了"
    )
    for entry in _DECLARED_SPECS:
        schema = build_spec("t", "d", (entry.spec,)).parameters["properties"][entry.spec.name]
        assert schema["type"] in VALID_JSON_TYPES, f"{entry.label} → {schema}"


@pytest.mark.parametrize("entry", _DECLARED_SPECS, ids=[e.label for e in _DECLARED_SPECS])
def test_declared_constraints_reach_the_schema_verbatim(entry: _DeclaredSpec) -> None:
    """声明里的**每一个**约束键都必须原样出现在发给模型的 schema 里。

    改前的断言只比 ``schema["type"]``，``minimum`` / ``enum`` 这些「模型真正会读的
    约束」完全没人看：``build_spec`` 少写一行 ``properties[p.name]["minimum"] = ...``
    也不会有任何用例转红。这里逐键比对（值也精确相等，不是「存在即可」）。
    """
    schema = build_spec("t", "d", (entry.spec,)).parameters["properties"][entry.spec.name]
    assert schema == _expected_schema(entry.declared), (
        f"{entry.label} 的 schema 与声明不符：\n"
        f"  声明 {entry.declared}\n  期望 {_expected_schema(entry.declared)}\n"
        f"  实际 {schema}"
    )
    # required 不在单个 property 里，而在 parameters["required"] 名单上 —— 一起钉住：
    # 必填参数漏进/误进名单，模型就会少传或多传一个实参，报错点离根因很远。
    parameters = build_spec("t", "d", (entry.spec,)).parameters
    in_required = entry.spec.name in parameters["required"]
    assert in_required is bool(entry.spec.required), (
        f"{entry.label} 的 required 声明是 {entry.spec.required}，"
        f"但 schema 的 required 名单 {parameters['required']} "
        f"{'包含' if in_required else '不含'}它"
    )


def test_numeric_lower_bounds_stay_meaningful() -> None:
    """数值下限必须落在「有意义」的区间里 —— 这是**绝对值**锚点，不是字面值锚点。

    为什么需要它：``test_declared_constraints_reach_the_schema_verbatim`` 的期望值
    来自声明原文，``minimum=1`` 改成 ``minimum=-100`` 时两边一起变，比对仍然相等 ——
    它能发现「约束被 build_spec 丢掉」，但发现不了「约束本身被改坏」。
    这里用领域事实（编号类参数的编号从 1 开始，0/负值没有意义）兜住后者：
    实测 ``reminders.py`` 的 ``minimum=1`` → ``minimum=-100`` 本条转红。
    锚的是区间（1..1000）而不是 1，合理调参不必改测试。
    """
    bounded = [entry for entry in _DECLARED_SPECS
               if entry.declared.get("minimum") is not None]
    assert bounded, "扫描没找到任何带 minimum 的参数 —— 扫描规则可能失效了"
    for entry in bounded:
        minimum = entry.spec.minimum
        assert minimum is not None and 1 <= minimum <= 1000, (
            f"{entry.label} 的下限是 {minimum}：编号/数量类参数从 1 起编号，"
            "0 或负值会把非法实参放行到工具层"
        )


#: 路径/ID 类参数的字符集是**安全边界**（这些值会拼进文件路径与查询），
#: 放宽哪怕一个字符（例如把 ``.`` 或 ``/`` 放进白名单）就是路径穿越的口子，
#: 所以这里逐字符钉死 —— 这是本文件唯一按字面值钉的地方，理由如上。
EXPECTED_PATTERNS: dict[str, str] = {
    "content.py:item_id": r"[A-Za-z0-9_:\-]{1,60}",
    "content.py:action_id": r"[A-Za-z0-9_\-]{1,40}",
    "reminders.py:text": r".{1,200}",
    "reminders.py:due_at_utc": r"\d{4}-\d{2}-\d{2}T.+",
    "reminders.py:idempotency_key": r".{1,64}",
    "reminders.py:timezone": r"[A-Za-z]+/[A-Za-z_]+",
    "weather.py:city": r"[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\s]{0,19}",
}

#: 每个带 pattern 的参数：一个必须**通过**的样本 + 几个必须**被拒**的样本。
#: 只钉 pattern 字面值还不够 —— 若 ``validate_args`` 哪天不再执行 pattern 检查，
#: 字符串再对也拦不住任何东西，所以同时验「拒绝/放行」的实际行为。
PATTERN_SAMPLES: dict[str, tuple[str, list[str]]] = {
    "content.py:item_id": ("story_01", ["../etc/passwd", "a/b", "a b", "x" * 61]),
    "content.py:action_id": ("wave", ["../x", "a/b", "a b", "x" * 41]),
    "reminders.py:text": ("喝水", [""]),
    "reminders.py:due_at_utc": ("2030-01-01T00:00:00Z", ["2030-01-01", "明天"]),
    "reminders.py:idempotency_key": ("k-1", [""]),
    "reminders.py:timezone": ("Asia/Shanghai", ["Asia Shanghai", "Asia/Shanghai/x"]),
    "weather.py:city": ("北京", ["", "A" * 21]),
}


def test_declared_patterns_match_the_pinned_contract() -> None:
    """每一处 ``pattern=`` 都必须等于钉死的字面值（改一个字符即转红）。

    为什么只能钉字面值、不能像 ``minimum`` 那样走 schema 比对：``build_spec``
    **不输出** ``pattern``（它只写 type / enum / minimum / maximum）—— 所以
    「pattern 有没有到达模型」这件事在本轮无法用 schema 断言证明，只能保证
    「声明里的 pattern 没被动过」。这是本组的一个已知空洞，详见交付报告。
    """
    actual = {entry.label: entry.declared["pattern"]
              for entry in _DECLARED_SPECS if "pattern" in entry.declared}
    assert actual.keys() == EXPECTED_PATTERNS.keys(), (
        f"带 pattern 的参数集合变了：多出 {sorted(actual.keys() - EXPECTED_PATTERNS.keys())}，"
        f"少了 {sorted(EXPECTED_PATTERNS.keys() - actual.keys())}（新增的要同步登记样本）"
    )
    for label, expected in EXPECTED_PATTERNS.items():
        assert actual[label] == expected, (
            f"{label} 的 pattern 变了：\n  期望 {expected!r}\n  实际 {actual[label]!r}\n"
            "路径/ID 类参数的字符集是安全边界，改动必须连带更新本表与样本"
        )


@pytest.mark.parametrize("entry", _DECLARED_SPECS, ids=[e.label for e in _DECLARED_SPECS])
def test_declared_patterns_are_actually_enforced_by_the_validator(
        entry: _DeclaredSpec) -> None:
    """``validate_args`` 必须真的按声明的 pattern 放行/拒绝（行为层，不只看字符串）。"""
    samples = PATTERN_SAMPLES.get(entry.label)
    if "pattern" not in entry.declared:
        assert samples is None, f"{entry.label} 没有 pattern 却登记了样本"
        return
    assert samples is not None, f"{entry.label} 带 pattern 但没登记拒绝样本"
    good, bad_values = samples

    registry = ToolRegistry()
    registry.register("probe", "探针", (entry.spec,), lambda args: {"ok": True})
    assert registry.validate_args("probe", {entry.spec.name: good}) == {
        entry.spec.name: good
    }, f"{entry.label} 把合法样本 {good!r} 拒了"
    for bad in bad_values:
        with pytest.raises(ToolValidationError):
            registry.validate_args("probe", {entry.spec.name: bad})


def test_spec_scanner_actually_found_the_known_specs() -> None:
    """守住「扫描规则本身失效」这一退化：空集或漏扫会让上面的断言变成恒绿。"""
    labels = {entry.label for entry in _DECLARED_SPECS}
    assert any("weather" in label for label in labels), labels
    assert any("reminders" in label for label in labels), labels
    assert any("content" in label for label in labels), labels
    # 关键：约束值本身也必须被扫到（改前的正则版本恰恰在这里是空集）
    assert sum(1 for e in _DECLARED_SPECS if e.declared.get("pattern")) == len(
        EXPECTED_PATTERNS
    ), f"扫到的 pattern 数量与登记表不符：{sorted(labels)}"
    assert sum(1 for e in _DECLARED_SPECS if e.declared.get("minimum") is not None) >= 1, (
        "一个 minimum 都没扫到 —— 约束值又被丢掉了"
    )


def test_toolspec_is_the_type_the_provider_sends() -> None:
    """确认 ``build_spec`` 的返回类型就是 provider 实际发出去的那个类型。

    若provider 侧另有转换，这一组测试就验错了对象。
    """
    spec = build_spec("t", "d", (ParamSpec("p", str),))
    assert isinstance(spec, ToolSpec)
    import inspect

    from host.ai import qwen_provider

    source = inspect.getsource(qwen_provider)
    assert '"type": "function"' in source, "provider 的请求形状变了，需重新核对本组测试"
