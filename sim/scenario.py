"""虚拟时间线上的故障注入与脚本化执行（模拟器的一等公民）。

场景 = 有序步骤序列，每步在虚拟毫秒 ``at_ms`` 触发：

- ``kind="host"``：主机向 MCU 发一行（等价串口写）。
- ``kind="inject"``：改写设备物理状态（拔线 / 急停 / 换角色 / 按键）。

``run`` 产出**逐行时间轴**（``t_ms`` / ``dir`` / ``line``），既可人工审阅，
也是黄金回放比对与回归断言的输入格式。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sim.clock import SimClock
from sim.controller import McController

INJECT_ACTIONS = ("dock", "estop", "role", "button_press", "button_release")


@dataclass(frozen=True, slots=True)
class Step:
    at_ms: int
    kind: str  # "host" | "inject"
    line: str = ""
    action: str = ""
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {"at_ms": self.at_ms, "kind": self.kind}
        if self.line:
            data["line"] = self.line
        if self.action:
            data["action"] = self.action
        if self.args:
            data["args"] = dict(self.args)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        return cls(
            at_ms=int(data["at_ms"]),
            kind=str(data["kind"]),
            line=str(data.get("line", "")),
            action=str(data.get("action", "")),
            args=dict(data.get("args", {})),
        )


def apply_injection(controller: McController, action: str, args: dict) -> None:
    if action == "dock":
        controller.dock.engaged = bool(args.get("engaged", True))
    elif action == "estop":
        controller.estop.pressed = bool(args.get("pressed", True))
    elif action == "role":
        controller.role_res.role = int(args["role"])
    elif action == "button_press":
        controller.buttons.press(int(args["n"]))
    elif action == "button_release":
        controller.buttons.release(int(args["n"]))
    else:
        raise ValueError(f"未知注入动作: {action}")


@dataclass(frozen=True, slots=True)
class Scenario:
    steps: tuple[Step, ...]
    controller_args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "controller_args": dict(self.controller_args),
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        return cls(
            steps=tuple(Step.from_dict(s) for s in data["steps"]),
            controller_args=dict(data.get("controller_args", {})),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def build_controller(self, clock=None) -> tuple[McController, object]:
        clock = clock if clock is not None else SimClock()
        return McController(clock, **self.controller_args), clock

    def run(self, controller: McController, clock) -> list[dict]:
        """执行场景，返回逐行时间轴 ``[{t_ms, dir, line}, ...]``。"""
        transcript: list[dict] = []
        for step in self.steps:
            advance_to(controller, clock, step.at_ms)
            settle(controller, clock)
            if step.kind == "host":
                controller.receive((step.line + "\n").encode("ascii"))
                transcript.append(
                    {"t_ms": clock.now_ms(), "dir": "host", "line": step.line}
                )
            elif step.kind == "inject":
                apply_injection(controller, step.action, step.args)
            elif step.kind != "wait":
                raise ValueError(f"未知步骤类型: {step.kind}")
            # 让「同一时刻」的写入与注入至少经历一次主循环，再消费完所有整行。
            controller.poll()
            settle(controller, clock)
            for t_ms, line in controller.take_events():
                transcript.append({"t_ms": t_ms, "dir": "mcu", "line": line})
        transcript.sort(key=lambda row: row["t_ms"])  # 稳定排序，得到真实时间轴
        return transcript


def advance_to(controller: McController, clock, at_ms: int) -> None:
    """把虚拟时钟推进到 ``at_ms``（只前进，不后退）。"""
    if at_ms > clock.now_ms():
        clock.advance(at_ms - clock.now_ms())


def settle(controller: McController, clock) -> None:
    """推进到当前时刻，并把 RX 里积压的整行按每圈 64 字节预算全部消费完。

    回放（``sim.session``）复用本函数，确保「录制」与「回放」用同一驱动节奏。
    """
    controller.run_until(clock.now_ms())
    guard = 0
    while controller.has_pending_input():
        controller.poll()
        guard += 1
        if guard > 1000:  # 防御性上限：正常输入不可能触发
            raise RuntimeError("RX 队列未能清空")


# --------------------------------------------------------------------------
# 内置场景：覆盖 20-mcu-simulator.md §6 的故障注入矩阵。
# --------------------------------------------------------------------------
def _host(at_ms: int, line: str) -> Step:
    return Step(at_ms=at_ms, kind="host", line=line)


def _inject(at_ms: int, action: str, **args) -> Step:
    return Step(at_ms=at_ms, kind="inject", action=action, args=args)


def _wait(at_ms: int) -> Step:
    return Step(at_ms=at_ms, kind="wait")


_ARMED_FRAME = (_host(300, "ARM 1"), _host(320, "FRAME 0 1500 1550 1500 1500"))


def _scenario(steps: tuple[Step, ...], **controller_args) -> Scenario:
    return Scenario(steps=steps, controller_args=controller_args)


BUILTIN_SCENARIOS: dict[str, Scenario] = {
    "interlock_dock_open": _scenario(
        (_inject(0, "dock", engaged=False), _host(300, "ARM 1"))
    ),
    "interlock_estop": _scenario(
        (_inject(0, "estop", pressed=True), _host(300, "ARM 1"))
    ),
    "interlock_role_mismatch": _scenario(
        (_inject(0, "role", role=2), _host(300, "ARM 1"))
    ),
    "interlock_unstable": _scenario(
        (_host(100, "ARM 1"), _host(400, "ARM 1"))
    ),
    "already_armed": _scenario(
        (_host(300, "ARM 1"), _host(320, "ARM 1"))
    ),
    "frame_out_of_range": _scenario(
        (_host(300, "ARM 1"), _host(320, "FRAME 0 1500 1500 1500 1700"))
    ),
    "frame_seq_regress": _scenario(
        (
            _host(300, "ARM 1"),
            _host(320, "FRAME 5 1500 1550 1500 1500"),
            _host(340, "FRAME 4 1500 1520 1500 1500"),
        )
    ),
    "frame_unarmed": _scenario((_host(300, "FRAME 0 1500 1550 1500 1500"),)),
    "watchdog_timeout": _scenario(_ARMED_FRAME + (_wait(700),)),
    "unplug_during_motion": _scenario(
        _ARMED_FRAME + (_inject(400, "dock", engaged=False),)
    ),
    "estop_during_motion": _scenario(
        _ARMED_FRAME + (_inject(400, "estop", pressed=True),)
    ),
    "swap_role_during_motion": _scenario(
        _ARMED_FRAME + (_inject(400, "role", role=2),)
    ),
    "line_overflow": _scenario((_host(300, "A" * 120),)),
    "unknown_command": _scenario((_host(300, "FOO"),)),
    "pwm_init_fail": _scenario((_host(300, "STATUS"),), pwm_ready=False),
}


def run_builtin(scenario_id: str, clock=None) -> list[dict]:
    scenario = BUILTIN_SCENARIOS[scenario_id]
    controller, clock = scenario.build_controller(clock)
    return scenario.run(controller, clock)
