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

import json

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
def _all_registered_specs() -> list[tuple[str, ParamSpec]]:
    """从 ``host/tools/*`` 收集所有实际注册用的 ``ParamSpec``。

    刻意用**源码扫描**而不是 import 真实 registry：那需要 LLM provider 凭据，
    而本组要验的是「声明的规格」本身能否产出合法 schema。
    """
    import pathlib
    import re

    pattern = re.compile(r"ParamSpec\(([^)]*)\)")
    found: list[tuple[str, ParamSpec]] = []
    for path in sorted(pathlib.Path("host/tools").glob("*.py")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            args = [a.strip() for a in match.group(1).split(",") if a.strip()]
            name = args[0].strip("\"'")
            kwargs: dict = {}
            for arg in args[1:]:
                if "=" not in arg:
                    continue
                key, value = arg.split("=", 1)
                kwargs[key.strip()] = value.strip()
            type_name = kwargs.get("type", "str")
            py_type = {"str": str, "int": int, "float": float, "bool": bool}[type_name]
            found.append((f"{path.name}:{name}",
                          ParamSpec(name, py_type,
                                    required=kwargs.get("required", "True") != "False")))
    return found


def test_every_registered_param_spec_produces_a_valid_schema() -> None:
    """线上全部工具参数的 schema 必须合法 —— 这是「组2 已修好」的直接证据。"""
    specs = _all_registered_specs()
    assert len(specs) >= 8, f"只扫到 {len(specs)} 处 ParamSpec，扫描规则可能失效了"
    for label, spec in specs:
        schema = build_spec("t", "d", (spec,)).parameters["properties"][spec.name]
        assert schema["type"] in VALID_JSON_TYPES, f"{label} → {schema}"


def test_spec_scanner_actually_found_the_known_specs() -> None:
    """守住「扫描规则本身失效」这一退化：空集或漏扫会让上一条变成恒绿。"""
    labels = {label for label, _ in _all_registered_specs()}
    assert any("weather" in label for label in labels), labels
    assert any("reminders" in label for label in labels), labels
    assert any("content" in label for label in labels), labels


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
