"""会话录制与回放：Spec §12 步骤 5「录制 → 回放 → 逐行比对」的证据载体。

会话文件是 JSONL，**首行为元信息**，其后为事件与期望输出：

    {"session": "toy-session-v1", "controller_args": {"pwm_ready": false}, "end_ms": 700}
    {"t_ms": 300, "dir": "host", "line": "ARM 1"}
    {"t_ms": 400, "dir": "inject", "action": "dock", "args": {"engaged": false}}
    {"t_ms": 700, "dir": "wait"}
    {"t_ms": 300, "dir": "mcu", "line": "OK ARM 1"}

修复 QA B1 的三条根因：

1. **初态可复现**：``controller_args`` 携带 ``pwm_ready`` 等构造参数；``dock`` /
   ``estop`` / ``role`` 的初值由 ``dir=inject`` 事件在 t=0 重放，非默认物理态场景
   （``interlock_dock_open`` / ``pwm_init_fail`` …）因此可被真实重建。
2. **时间推进到最后一步**：``end_ms`` 为场景最后一步时刻，回放推进到此，覆盖末条
   host 行之后的**看门狗 / 上报**等时间驱动输出。
3. **排空 RX**：复用 ``scenario.settle``（每圈 64 字节预算循环消费，直到无待处理字节）。

回放对每个事件做「推进 → settle → 作用 → poll → settle」，与 ``Scenario.run`` 完全
同节奏，因此逐行（含时间戳）可复现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sim.clock import SimClock
from sim.controller import McController
from sim.scenario import Scenario, advance_to, apply_injection, settle
from sim.tcp_bridge import make_controller

SESSION_TAG = "toy-session-v1"
EVENT_DIRS = ("host", "inject", "wait")
MCU_DIR = "mcu"


@dataclass(frozen=True, slots=True)
class Session:
    """一次可回放会话：初态 + 有序事件 + 期望的 MCU 输出（含时间戳）。"""

    controller_args: dict = field(default_factory=dict)
    events: tuple[dict, ...] = ()
    expected: tuple[dict, ...] = ()
    end_ms: int = 0

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_scenario(cls, scenario: Scenario) -> Session:
        """跑一遍场景，把输入与输出落成可回放会话（录制）。"""
        controller, clock = scenario.build_controller()
        transcript = scenario.run(controller, clock)
        events = tuple(_event_row(step) for step in scenario.steps)
        expected = tuple(row for row in transcript if row["dir"] == MCU_DIR)
        end_ms = max((step.at_ms for step in scenario.steps), default=0)
        return cls(
            controller_args=dict(scenario.controller_args),
            events=events,
            expected=expected,
            end_ms=end_ms,
        )

    # ------------------------------------------------------------ 序列化
    def to_lines(self) -> list[str]:
        head = {
            "session": SESSION_TAG,
            "controller_args": dict(self.controller_args),
            "end_ms": int(self.end_ms),
        }
        rows = [head, *self.events, *self.expected]
        return [json.dumps(row, ensure_ascii=False) for row in rows]

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(self.to_lines()) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Session:
        raw = [
            line
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not raw:
            raise ValueError(f"空会话文件: {path}")
        first = json.loads(raw[0])
        if "session" not in first:
            return _load_legacy(raw)
        events: list[dict] = []
        expected: list[dict] = []
        for line in raw[1:]:
            row = json.loads(line)
            direction = row.get("dir")
            if direction == MCU_DIR:
                expected.append(row)
            elif direction in EVENT_DIRS:
                events.append(row)
            else:
                raise ValueError(f"未知行方向: {direction!r}")
        end_ms = int(first.get("end_ms") or _last_ms(events))
        return cls(
            controller_args=dict(first.get("controller_args", {})),
            events=tuple(events),
            expected=tuple(expected),
            end_ms=end_ms,
        )


def _event_row(step) -> dict:
    if step.kind == "host":
        return {"t_ms": step.at_ms, "dir": "host", "line": step.line}
    if step.kind == "inject":
        return {"t_ms": step.at_ms, "dir": "inject", "action": step.action,
                "args": dict(step.args)}
    return {"t_ms": step.at_ms, "dir": "wait"}


def _last_ms(events: tuple[dict, ...]) -> int:
    return max((int(row["t_ms"]) for row in events), default=0)


def _load_legacy(raw: list[str]) -> Session:
    """兼容旧格式（无元信息头）：仅 host / mcu 两种行。"""
    events: list[dict] = []
    expected: list[dict] = []
    for line in raw:
        row = json.loads(line)
        if row.get("dir") == MCU_DIR:
            expected.append(row)
        else:
            events.append(row)
    return Session(events=tuple(events), expected=tuple(expected), end_ms=_last_ms(tuple(events)))


def _apply(controller: McController, row: dict) -> None:
    if row["dir"] == "host":
        controller.receive((row["line"] + "\n").encode("ascii"))
    elif row["dir"] == "inject":
        apply_injection(controller, row["action"], row.get("args", {}))
    # dir == "wait"：只推进时间，无动作。


def replay(session: Session) -> list[tuple[int, str]]:
    """回放会话，返回重建出的 ``(t_ms, line)`` 序列（与录制同节奏）。"""
    controller = make_controller(SimClock(), **_known_args(session.controller_args))
    produced: list[tuple[int, str]] = []
    for row in session.events:
        advance_to(controller, controller.clock, int(row["t_ms"]))
        settle(controller, controller.clock)
        _apply(controller, row)
        controller.poll()
        settle(controller, controller.clock)
        produced.extend(controller.take_events())
    advance_to(controller, controller.clock, int(session.end_ms))
    settle(controller, controller.clock)
    produced.extend(controller.take_events())
    return produced


def _known_args(controller_args: dict) -> dict:
    """只把 ``make_controller`` 认识的键透传，多余的忽略（向前兼容）。"""
    allowed = {"pwm_ready", "role", "docked", "estop_ok"}
    return {key: value for key, value in controller_args.items() if key in allowed}
