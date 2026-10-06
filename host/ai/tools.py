"""工具白名单注册与参数校验（AC-18：参数非法必须**本地拒绝**，不得编造）。

- 模型可以「请求」调用任何工具名，但只有本注册表里的白名单工具可被执行。
- 参数经 **显式白名单**校验：未知字段、缺必填、类型错、越界、非法枚举一律
  ``ToolValidationError``，绝不把未校验的参数交给处理器。
- 处理器由 ``host/tools/*`` 提供；本模块只管注册与校验，不含业务实现。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host.ai.providers import ToolSpec
from host.core.errors import ToolValidationError

Handler = Callable[[dict[str, Any]], dict[str, Any]]

# Python 类型 → JSON Schema ``type`` 关键字。
#
# **不能直接用 ``p.type.__name__``**：那会产出 ``"str"`` / ``"int"`` / ``"float"`` /
# ``"bool"``，而 JSON Schema（draft 2020-12，OpenAI function-calling 沿用）只认
# ``"string"`` / ``"integer"`` / ``"number"`` / ``"boolean"``。这份 schema 由
# ``host/ai/qwen_provider.py`` 原样发给模型，所以发出去的是**每个工具参数都非法**的
# type 值 —— 模型据此生成参数时更容易把数字当字符串，反过来触发本地类型拒绝。
JSON_TYPE_NAMES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}
#: 只接受**数值语义**的类型（可与 ``minimum``/``maximum`` 组合）。
NUMERIC_TYPES: frozenset[type] = frozenset({int, float})


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """单个参数的校验规格（白名单）。

    ``__post_init__`` 拒绝「非数值类型 + 数值边界」这种组合：``validate_args`` 里
    紧接着的 ``value < p.minimum`` 对字符串会抛 ``TypeError``，而那不是
    ``ToolValidationError`` —— 会一路逃过 ``host/ai/router.py`` 只捕获
    ``ToolValidationError`` 的白名单拒绝路径，把本地的「参数非法」变成整轮对话失败。
    与其等到运行时炸，不如在规格构造时就拒（这是**注册期**错误，测试与启动都能发现）。
    """

    name: str
    type: type
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    pattern: str | None = None

    def __post_init__(self) -> None:
        if self.type not in JSON_TYPE_NAMES:
            raise ValueError(
                f"参数 {self.name} 的类型 {self.type.__name__!r} 没有对应的 JSON Schema "
                f"type 关键字；支持 {sorted(t.__name__ for t in JSON_TYPE_NAMES)}"
            )
        if self.type not in NUMERIC_TYPES and (self.minimum is not None
                                               or self.maximum is not None):
            raise ValueError(
                f"参数 {self.name} 是 {self.type.__name__} 类型，不能配数值边界 "
                f"（minimum/maximum）—— 比较时会抛 TypeError 并逃出白名单拒绝路径"
            )


@dataclass(slots=True)
class _Tool:
    spec: ToolSpec
    params: tuple[ParamSpec, ...]
    handler: Handler


def _type_ok(value: Any, expected: type) -> bool:
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def build_spec(name: str, description: str, params: tuple[ParamSpec, ...]) -> ToolSpec:
    """由 ``ParamSpec`` 生成给模型看的 JSON-schema 风格描述。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        properties[p.name] = {"type": JSON_TYPE_NAMES[p.type]}
        if p.choices:
            properties[p.name]["enum"] = list(p.choices)
        if p.minimum is not None:
            properties[p.name]["minimum"] = p.minimum
        if p.maximum is not None:
            properties[p.name]["maximum"] = p.maximum
        if p.required:
            required.append(p.name)
    return ToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
    )


class ToolRegistry:
    """工具白名单：注册、列举与**二次校验后**执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, _Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        params: tuple[ParamSpec, ...],
        handler: Handler,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"工具已注册: {name}")
        self._tools[name] = _Tool(build_spec(name, description, params), params, handler)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def validate_args(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """校验并归一化参数；任何不合格都抛 ``ToolValidationError``。"""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolValidationError(f"非白名单工具: {name}")
        if not isinstance(arguments, dict):
            raise ToolValidationError("工具参数必须是对象")

        allowed = {p.name for p in tool.params}
        unknown = set(arguments) - allowed
        if unknown:
            raise ToolValidationError(f"含未白名单参数: {sorted(unknown)}")

        normalized: dict[str, Any] = {}
        for p in tool.params:
            if p.name not in arguments:
                if p.required:
                    raise ToolValidationError(f"缺少必填参数: {p.name}")
                continue
            value = arguments[p.name]
            if not _type_ok(value, p.type):
                # 报**规范名**而非 Python 名：调用方看到的是模型给的 arguments，
                # 模型看到的是 schema 里的 ``"string"``。报 ``str`` 会让排障时
                # 两边对不上号。
                raise ToolValidationError(
                    f"参数 {p.name} 类型应为 {JSON_TYPE_NAMES[p.type]}"
                )
            if p.minimum is not None and value < p.minimum:
                raise ToolValidationError(f"参数 {p.name} 小于下限 {p.minimum}")
            if p.maximum is not None and value > p.maximum:
                raise ToolValidationError(f"参数 {p.name} 大于上限 {p.maximum}")
            if p.choices is not None and value not in p.choices:
                raise ToolValidationError(f"参数 {p.name} 不在允许取值内")
            if p.pattern is not None and not re.fullmatch(p.pattern, str(value)):
                raise ToolValidationError(f"参数 {p.name} 格式非法")
            normalized[p.name] = value
        return normalized

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """校验通过后执行处理器；返回值必须是 JSON 可序列化对象。"""
        normalized = self.validate_args(name, arguments)
        result = self._tools[name].handler(normalized)
        if not isinstance(result, dict):
            raise ToolValidationError(f"工具 {name} 返回值必须是对象")
        return result
