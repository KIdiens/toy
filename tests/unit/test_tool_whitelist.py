"""工具白名单与二次参数校验（AC-18）：非法参数**本地拒绝**，超时**绝不编造**。"""
from __future__ import annotations

import pytest
from host.ai.tools import ParamSpec, ToolRegistry
from host.core.errors import ToolValidationError
from host.tools import weather


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "greet", "跟小朋友打招呼",
        (
            ParamSpec("name", str, required=True, pattern=r".{1,10}"),
            ParamSpec("times", int, required=False, minimum=1, maximum=3),
            ParamSpec("mood", str, required=False, choices=("happy", "sad")),
        ),
        lambda args: {"ok": True, "echo": args["name"], "times": args.get("times", 1)},
    )
    return registry


def test_unknown_tool_is_rejected_locally() -> None:
    with pytest.raises(ToolValidationError, match="非白名单工具"):
        _registry().invoke("delete_everything", {})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ToolValidationError, match="未白名单参数"):
        _registry().validate_args("greet", {"name": "小星", "shell": "rm -rf /"})


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ToolValidationError, match="缺少必填参数"):
        _registry().validate_args("greet", {})


def test_wrong_type_is_rejected() -> None:
    with pytest.raises(ToolValidationError, match="类型应为 int"):
        _registry().validate_args("greet", {"name": "小星", "times": "很多"})
    # 布尔不是整数：避免 True/1 混淆这类静默错误
    with pytest.raises(ToolValidationError, match="类型应为 int"):
        _registry().validate_args("greet", {"name": "小星", "times": True})


def test_out_of_range_is_rejected() -> None:
    with pytest.raises(ToolValidationError, match="小于下限"):
        _registry().validate_args("greet", {"name": "小星", "times": 0})
    with pytest.raises(ToolValidationError, match="大于上限"):
        _registry().validate_args("greet", {"name": "小星", "times": 99})


def test_enum_and_pattern_are_enforced() -> None:
    with pytest.raises(ToolValidationError, match="不在允许取值内"):
        _registry().validate_args("greet", {"name": "小星", "mood": "angry"})
    with pytest.raises(ToolValidationError, match="格式非法"):
        _registry().validate_args("greet", {"name": ""})


def test_valid_args_are_normalized_and_dispatched() -> None:
    result = _registry().invoke("greet", {"name": "小星", "times": 2})
    assert result == {"ok": True, "echo": "小星", "times": 2}


def test_handler_returning_non_object_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register("bad", "返回非对象", (ParamSpec("x", int),), lambda args: [1, 2, 3])
    with pytest.raises(ToolValidationError, match="返回值必须是对象"):
        registry.invoke("bad", {"x": 1})


# ------------------------------------------------------ 天气：超时不编造（AC-18）
@pytest.fixture()
def weather_registry() -> ToolRegistry:
    registry = ToolRegistry()
    weather.register(registry, weather.StubWeatherClient(fail_cities=("雷城",)))
    return registry


def test_weather_success_returns_real_stub_values(weather_registry: ToolRegistry) -> None:
    result = weather_registry.invoke("get_weather", {"city": "北京"})
    assert result["ok"] is True
    assert result["condition"] == "晴" and result["temp_c"] == 24.0
    assert result["fabricated"] is False


def test_weather_timeout_reports_error_not_fabrication(weather_registry: ToolRegistry) -> None:
    result = weather_registry.invoke("get_weather", {"city": "雷城"})
    assert result["ok"] is False
    assert result["fabricated"] is False
    assert result["error"], "必须给出可朗读的明确原因"
    assert "temp_c" not in result and "condition" not in result, "失败时绝不能带出任何天气数值"


def test_weather_unknown_city_reports_error(weather_registry: ToolRegistry) -> None:
    result = weather_registry.invoke("get_weather", {"city": "不存在的城"})
    assert result["ok"] is False and result["fabricated"] is False


def test_weather_rejects_illegal_city_before_handler(weather_registry: ToolRegistry) -> None:
    with pytest.raises(ToolValidationError, match="格式非法"):
        weather_registry.invoke("get_weather", {"city": "../../etc/passwd"})
