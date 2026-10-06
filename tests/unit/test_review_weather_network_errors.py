"""``get_weather`` 的网络异常必须降级成 ``ok=False`` 而不是打穿整轮对话（组 4）。

## 缺陷

``weather.py`` 原先只``except TimeoutError``。而真实 provider 走``urllib``
/ ``http.client`` 时，网络层失败的绝大多数异常**都不在``TimeoutError`` 族里**：

| 真实场景 | 异常 | 是否 ``TimeoutError`` |
|---|---|---|
| DNS 解析失败 | ``urllib.error.URLError`` |否 |
| 连接被拒 / 重置 | ``ConnectionRefusedError`` / ``ConnectionResetError`` | 否 |
| TLS 握手失败 | ``ssl.SSLError`` | 否 |
| socket 超时 | ``socket.timeout`` | 是（且它是 ``OSError`` 子类） |
| HTTP 4xx/5xx | ``urllib.error.HTTPError`` | 否 |

实测（``urllib.error.URLError`` 走完整 ``AiRouter.respond_text``）：
异常一路逃到 ``router.py:128`` —— 那里只捕 ``ToolValidationError``，
于是 ``URLError`` 穿透工具层、穿透 AI 回合，**用户这一轮彻底没答复**。

## 为什么不能写 ``except Exception``

本模块第 1 行的红线是「绝不编造」。``except Exception`` 会把
``client.fetch`` 内部的 ``AttributeError``（client 没有 ``fetch``）、
``NameError``、``TypeError`` 一起吞成「天气服务超时」——
那不是编造天气数据，但**它伪造了故障原因**，让真实 bug 永远查不出来，
且恰好掩盖在「网络不稳定」这个最容易被原谅的解释下。

同理也不能整个捕获 ``http.client.HTTPException``：它是混合体——
``BadStatusLine`` / ``IncompleteRead`` 确实是对端问题，但
``InvalidURL``（URL 写错）、``UnknownProtocol``（协议不支持）、
``ImproperConnectionState``及其子类是**调用方的代码 bug**。

所以捕获范围精确取 ``OSError``：真provider 的传输层失败全在这一族里
（已实测 ``URLError`` / ``HTTPError`` / ``SSLError`` / ``ConnectionResetError``
均为 ``OSError`` 子类，且 ``TimeoutError`` 本身就是 ``OSError`` 子类）。

## 判别力

只断言「抛 ``OSError`` 时返回 ``ok=False``」是不够的——那**恒绿**，
因为任何 ``except`` 都能通过。所以本文件另配两条反向约束：

- ``test_programming_errors_are_not_swallowed``：``AttributeError`` /
  ``TypeError`` 必须**逃出去**。它是上文的判别力来源。
- ``test_stub_timeout_still_says_timeout``：保住原有行为不回退。
"""
from __future__ import annotations

import ssl
import urllib.error

import pytest
from host.ai.providers import LlmReply
from host.ai.router import AiRouter
from host.ai.tools import ToolRegistry, ToolValidationError
from host.tools.weather import (
    StubWeatherClient,
    register,
)


class _RaisingClient:
    """按需抛指定异常的 ``WeatherClient``；``exc`` 为 None 时返回正常结果。"""

    def __init__(self, exc: BaseException | None) -> None:
        self.exc = exc

    def fetch(self, city: str, *, timeout: float):
        if self.exc is not None:
            raise self.exc
        from host.tools.weather import WeatherResult

        return WeatherResult(ok=True, city=city, condition="晴", temp_c=24.0)


def _registry_with(exc: BaseException | None) -> ToolRegistry:
    registry = ToolRegistry()
    register(registry, _RaisingClient(exc))  # type: ignore[arg-type]
    return registry


def _invoke(exc: BaseException | None) -> dict:
    return _registry_with(exc).invoke("get_weather", {"city": "北京"})


# =====================================================================
# 1. 真实 provider 的传输层失败必须降级，不得逃出
# =====================================================================
@pytest.mark.parametrize("exc", [
    urllib.error.URLError("Name or service not known"),
    urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, None),
    ConnectionRefusedError("Connection refused"),
    ConnectionResetError("Connection reset by peer"),
    ssl.SSLError("certificate verify failed"),
    TimeoutError("timed out"),
    TimeoutError("天气服务超时: 北京"),
    OSError("网络不可达"),
])
def test_transport_failures_degrade_instead_of_raising(exc: BaseException) -> None:
    """真provider 的传输层失败全在 ``OSError`` 族内，必须降级成 ``ok=False``。"""
    result = _invoke(exc)
    assert result["ok"] is False, result
    assert result["fabricated"] is False, "失败路径绝不能声称编造了数据"
    # 降级文案要能让人知道是「服务不可达」而不是「查不到这个城市」
    assert any(w in result["error"] for w in ("超时", "不可", "服务")), result


def test_degradation_never_invents_weather_fields() -> None:
    """降级返回里绝不能出现温度/天气字段 —— 那是编造。"""
    result = _invoke(urllib.error.URLError("down"))
    assert "temp_c" not in result, result
    assert "condition" not in result, result


# =====================================================================
# 2. 判别力：代码 bug 必须逃出去（否则上面那组恒绿）
# =====================================================================
@pytest.mark.parametrize("exc", [
    AttributeError("'NoneType' object has no attribute 'fetch'"),
    TypeError("fetch() got an unexpected keyword argument 'timeout'"),
    NameError("name 'api_key' is not defined"),
    KeyError("city"),
    ZeroDivisionError("division by zero"),
])
def test_programming_errors_are_not_swallowed(exc: BaseException) -> None:
    """``fetch`` 内部的代码 bug 必须抛出，不得被伪装成「天气服务超时」。

    这是第 1 节的判别力来源：若handler 捕获的是 ``Exception``，
    这些会全部变成 ``ok=False``，第 1 节就恒绿了。
    """
    with pytest.raises(type(exc)):
        _invoke(exc)


def test_http_protocol_errors_are_not_all_treated_as_network_outages() -> None:
    """``InvalidURL`` / ``UnknownProtocol`` 是**调用方代码 bug**，不该被吞。

    ``http.client.HTTPException`` 是混合体（``BadStatusLine`` 属对端问题，
    ``InvalidURL`` 属代码 bug），所以本模块刻意**不**捕获整个族。
    本条钉住这个选择：将来若有人图省事加上 ``HTTPException``，这里转红。
    """
    import http.client

    for exc in (http.client.InvalidURL("ftp://x"), http.client.UnknownProtocol("gopher")):
        with pytest.raises(type(exc)):
            _invoke(exc)


# =====================================================================
# 3. 原有行为不回退
# =====================================================================
def test_stub_timeout_still_says_timeout() -> None:
    """``StubWeatherClient(fail_cities=...)`` 走的还是原路径，文案不变。

    这条同时钉住 ``except`` 子句的**顺序**：
    ``TimeoutError`` 是 ``OSError`` 的子类，两条子句写反了的话
    超时会被后一条接走，文案退化成「不可用（TimeoutError）」。
    """
    registry = ToolRegistry()
    register(registry, StubWeatherClient(fail_cities=("雷城",)))
    result = registry.invoke("get_weather", {"city": "雷城"})
    assert result == {"ok": False, "error": "天气服务超时，暂时查不到",
                      "fabricated": False}


def test_timeout_and_other_os_errors_get_distinguishable_messages() -> None:
    """超时与非超时故障的文案必须可区分 —— 都报「超时」就没法排障。

    判别力来源：若两条子句合并成 ``except OSError`` 统一文案，
    超时这条会红；若都报「超时」，另一条会红。
    """
    timeout = _invoke(TimeoutError("timed out"))
    dns = _invoke(urllib.error.URLError("Name or service not known"))
    assert timeout["error"] != dns["error"], "两类故障文案相同，排障时会误判"
    assert "超时" in timeout["error"]
    assert "URLError" in dns["error"], dns


def test_unknown_city_is_still_reported_as_not_listed() -> None:
    """未收录城市与网络故障是**两件不同的事**，不能混成同一个文案。

    这是降级路径的判别力：若把所有失败都写成「服务超时」，
    「未收录」这个可行动信息就丢了。
    """
    registry = ToolRegistry()
    register(registry, StubWeatherClient())
    result = registry.invoke("get_weather", {"city": "拉萨"})
    assert result["ok"] is False
    assert "未收录" in result["error"], result
    assert "超时" not in result["error"], result


def test_success_path_unchanged() -> None:
    """成功路径不受影响（防止为了加捕获把返回值结构改了）。"""
    registry = ToolRegistry()
    register(registry, StubWeatherClient())
    result = registry.invoke("get_weather", {"city": "北京"})
    assert result == {"ok": True, "city": "北京", "condition": "晴",
                      "temp_c": 24.0, "fabricated": False}


# =====================================================================
# 4. 端到端：网络故障不得打穿整轮对话
# =====================================================================
class _FakeLlm:
    """第一回合要求调天气，第二回合给最终答复。"""

    def __init__(self) -> None:
        self.rounds = 0

    def complete(self, messages, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            call = type("Call", (), {"name": "get_weather",
                                     "arguments": {"city": "北京"}})()
            return LlmReply(text="", tool_calls=(call,))
        return LlmReply(text="查不到北京的天气", tool_calls=())


class _FakeTts:
    def synthesize(self, text, voice, lang=None):
        return type("Clip", (), {"text": text, "path": None})()


def test_network_failure_does_not_break_the_whole_turn() -> None:
    """``router.py:128`` 只捕 ``ToolValidationError`` —— 异常逃出即用户没答复。

    这是本组缺陷的**真实后果**，也是单测第1 节证明不了的那一半。
    """
    registry = _registry_with(urllib.error.URLError("Name or service not known"))
    router = AiRouter(None, _FakeLlm(), _FakeTts(), registry)  # type: ignore[arg-type]
    result = router.respond_text("北京天气")
    assert "get_weather" in result.tool_calls, result
    assert result.reply_text, "网络故障不该让这一轮彻底没答复"


def test_tool_result_is_visible_to_the_model_on_failure() -> None:
    """降级结果必须真的进到 ``messages`` 里，模型才可能据此改口。

    早前 ``_run_tools`` 里有死赋值（``reply = current``），工具结果压根到不了
    用户。这里钉住「失败结果也被回灌」，防止再次退化。
    """
    registry = _registry_with(urllib.error.URLError("down"))
    seen: list = []

    class _SpyLlm(_FakeLlm):
        def complete(self, messages, tools=None):
            seen.extend(messages)
            return super().complete(messages, tools)

    router = AiRouter(None, _SpyLlm(), _FakeTts(), registry)  # type: ignore[arg-type]
    router.respond_text("北京天气")
    tool_messages = [m for m in seen if m.role == "tool"]
    assert tool_messages, seen
    assert "天气服务" in tool_messages[-1].content, tool_messages[-1]


# =====================================================================
# 5. 参数校验仍在上游拦（不该被本模块捕获）
# =====================================================================
def test_argument_validation_still_raises_tool_validation_error() -> None:
    """``city`` 格式非法应在 ``validate_args`` 就被拒，不进 ``fetch``。"""
    registry = _registry_with(None)
    with pytest.raises(ToolValidationError):
        registry.invoke("get_weather", {"city": "1"})