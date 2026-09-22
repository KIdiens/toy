"""模拟外设：到位开关、急停、角色电阻、三按键（含 30ms 去抖）。

与 ``motion_controller.ino`` 的对应关系：

- ``docked()``  = ``digitalRead(dockPin) == LOW``（LOW 表示公仔装到位）。
- ``healthy()`` = ``digitalRead(estopPin) == LOW``（LOW 表示急停未按下）。
- ``readRole()``= ``analogReadMilliVolts(idPin)`` 套三窗口 150-450 / 850-1250 /
  1450-1900 → 1/2/3，否则 0。
- 按键：``digitalRead`` 去抖 30ms，下降沿（LOW）发出 ``BUTTON n``。

角色电阻采用理想分压（10k 上拉至 3.3V）：``mV = 3300 * R / (10000 + R)``。
窗口本身**未实测**（架构 §10.1 C4），此处只验证「窗口映射 → 稳定 → 联锁」逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass

# 公仔接地电阻（欧姆），固件注释点名 1k / 4.7k / 10k。
ROLE_RESISTORS: dict[int, int] = {1: 1000, 2: 4700, 3: 10000}
PULLUP_OHM = 10000
VCC_MV = 3300

# ADC 窗口（毫伏），逐字来自固件 readRole()。
ROLE_WINDOWS: tuple[tuple[int, int, int], ...] = (
    (150, 450, 1),
    (850, 1250, 2),
    (1450, 1900, 3),
)

LOW = 0
HIGH = 1
DEBOUNCE_MS = 30


def role_millivolts(role: int) -> float:
    """给定公仔角色，算理论分压（毫伏）。role=0（未装）返回 0。"""
    if role == 0:
        return 0.0
    if role not in ROLE_RESISTORS:
        raise ValueError(f"未知角色电阻档位: {role}")
    resistance = ROLE_RESISTORS[role]
    return VCC_MV * resistance / (PULLUP_OHM + resistance)


def role_from_millivolts(millivolts: float) -> int:
    """把毫伏映射到角色号；不在任何窗口内返回 0（与固件一致）。"""
    for low, high, role in ROLE_WINDOWS:
        if low <= millivolts <= high:
            return role
    return 0


@dataclass(slots=True)
class Dock:
    """到位开关。``engaged=True`` 表示公仔已装到位（digitalRead == LOW）。"""

    engaged: bool = True

    def docked(self) -> bool:
        return self.engaged


@dataclass(slots=True)
class Estop:
    """急停。``pressed=True`` 表示急停按下（healthy 变为 False）。"""

    pressed: bool = False

    def healthy(self) -> bool:
        return not self.pressed


@dataclass(slots=True)
class RoleResistor:
    """角色识别电阻。``role`` 为当前装上的公仔角色，0 表示未装/识别失败。"""

    role: int = 1

    def read(self) -> int:
        return role_from_millivolts(role_millivolts(self.role))

    def millivolts(self) -> float:
        return role_millivolts(self.role)


@dataclass(slots=True)
class _ButtonPin:
    pin: int = HIGH  # 当前电平（注入方改写）
    raw: int = HIGH  # 上次读到的电平（buttonRaw）
    stable: int = HIGH  # 去抖后的稳定电平（buttonStable）
    since: int = 0  # 上次原始电平变化时刻（buttonSince）


class Buttons:
    """三按键去抖，逐字复刻固件逻辑（30ms 稳定期，下降沿发事件）。"""

    def __init__(self, *, debounce_ms: int = DEBOUNCE_MS) -> None:
        self._pins = [_ButtonPin() for _ in range(3)]
        self._debounce_ms = debounce_ms

    def press(self, index: int) -> None:
        """注入按下（LOW）。index 为 1..3。"""
        self._pin(index).pin = LOW

    def release(self, index: int) -> None:
        """注入松开（HIGH）。index 为 1..3。"""
        self._pin(index).pin = HIGH

    def raw_level(self, index: int) -> int:
        return self._pin(index).pin

    def stable_level(self, index: int) -> int:
        return self._pin(index).stable

    def update(self, now_ms: int) -> list[int]:
        """推进去抖，返回本次新产生的按键号（可能多个，也可能为空）。"""
        fired: list[int] = []
        for i, pin in enumerate(self._pins, start=1):
            value = pin.pin
            if value != pin.raw:
                pin.raw = value
                pin.since = now_ms
            if now_ms - pin.since >= self._debounce_ms and value != pin.stable:
                pin.stable = value
                if value == LOW:
                    fired.append(i)
        return fired

    def _pin(self, index: int) -> _ButtonPin:
        if not 1 <= index <= 3:
            raise ValueError("按键编号只能是 1、2、3")
        return self._pins[index - 1]
