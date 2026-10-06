"""``get_weather`` 工具：可桩、**超时必须明确报错不得编造**（AC-18）。

- 网络/超时/未收录一律返回 ``ok=False`` + 明确 ``error``；**绝不**编造温度或天气。
- 参数经 ``host/ai/tools.py`` 白名单校验后才会到达本处理器。
- 无网络阶段使用 ``StubWeatherClient``（确定性表），真实现只需替换 ``WeatherClient``。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from host.ai.tools import ParamSpec, ToolRegistry

DEFAULT_TIMEOUT_S = 3.0
CITY_PATTERN = r"[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\s]{0,19}"


@dataclass(frozen=True, slots=True)
class WeatherResult:
    ok: bool
    city: str = ""
    condition: str = ""
    temp_c: float | None = None
    error: str = ""


@runtime_checkable
class WeatherClient(Protocol):
    def fetch(self, city: str, *, timeout: float) -> WeatherResult: ...


class StubWeatherClient:
    """确定性天气桩：命中表返回，``fail_cities`` 模拟超时，其余返回「未收录」。"""

    def __init__(
        self,
        table: Mapping[str, tuple[str, float]] | None = None,
        *,
        fail_cities: tuple[str, ...] = (),
    ) -> None:
        self.table = dict(table or {"北京": ("晴", 24.0), "上海": ("多云", 26.0)})
        self.fail_cities = tuple(fail_cities)

    def fetch(self, city: str, *, timeout: float) -> WeatherResult:
        if city in self.fail_cities:
            raise TimeoutError(f"天气服务超时: {city}")
        if city not in self.table:
            return WeatherResult(ok=False, city=city, error=f"未收录城市: {city}")
        condition, temp_c = self.table[city]
        return WeatherResult(ok=True, city=city, condition=condition, temp_c=temp_c)


def make_handler(client: WeatherClient, *, timeout_s: float = DEFAULT_TIMEOUT_S):
    """构造处理器：任何失败路径都返回 ``ok=False``，不产生任何捏造数据。

    捕获范围精确取 ``OSError``，理由有两条，都不是风格问题：

    1. **必须放宽**：真实 provider 走 ``urllib`` / ``http.client``，
       DNS 失败是 ``URLError``、连接被拒是 ``ConnectionRefusedError``、
       TLS 失败是 ``SSLError`` —— 全都不是 ``TimeoutError``。
       而 ``router.py`` 只捕 ``ToolValidationError``，异常一旦逃出工具层，
       就会打穿整个 AI 回合（用户这一轮彻底没答复）。
       实测 ``TimeoutError`` 本身就是 ``OSError`` 子类，
       所以 ``except OSError`` 是严格放宽而非替换。

    2. **不能更宽**：``except Exception`` 会把 ``fetch`` 内部的
       ``AttributeError``（client 没有 ``fetch``）、``NameError``、
       ``TypeError`` 一起吞成「天气服务超时」。那不是编造天气数据，
       但它**伪造了故障原因**，让真实 bug 藏在「网络不稳定」这个
       最容易被原谅的解释下再也查不出来。
       同样刻意**不**捕获整个 ``http.client.HTTPException``：
       它是混合体—— ``BadStatusLine`` / ``IncompleteRead`` 属对端问题，
       而 ``InvalidURL`` / ``UnknownProtocol`` 属调用方代码 bug。
    """

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        city = str(args["city"])
        try:
            result = client.fetch(city, timeout=timeout_s)
        except TimeoutError:
            return {"ok": False, "error": "天气服务超时，暂时查不到", "fabricated": False}
        except OSError as exc:
            # 非超时的传输层失败（DNS / 连接被拒 / TLS）。
            # **不能也报「超时」**：那是运维上最常见的信号，报准了才能定位；
            # 混为一谈会让间歇性故障无从下手。带上异常类型名即可区分。
            return {
                "ok": False,
                "error": f"天气服务不可用，暂时查不到（{type(exc).__name__}）",
                "fabricated": False,
            }
        if not result.ok:
            return {"ok": False, "error": result.error, "fabricated": False}
        return {
            "ok": True,
            "city": result.city,
            "condition": result.condition,
            "temp_c": result.temp_c,
            "fabricated": False,
        }

    return handler


def register(registry: ToolRegistry, client: WeatherClient, *,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
    registry.register(
        "get_weather",
        "查询某城市当前天气；查不到必须说明查不到，不得编造。",
        (ParamSpec("city", str, required=True, pattern=CITY_PATTERN),),
        make_handler(client, timeout_s=timeout_s),
    )
