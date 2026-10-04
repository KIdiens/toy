"""模拟器故障路径全覆盖（架构 §11 / 20-mcu-simulator §6）+ 固件语义细节。

覆盖：联锁四类、ALREADY_ARMED、FRAME 三类、看门狗、运动中拔线/急停/换角色、
行溢出、未知命令、PWM 初始化失败；以及 sscanf 解析细节、previousSeq 不重置、
200ms 稳定期、PWM 限速、64B/循环预算、STATUS 不维持运动。
"""
from __future__ import annotations

import pytest
from sim.clock import SimClock
from sim.controller import McController
from sim.scenario import BUILTIN_SCENARIOS

FAULT = "FAULT INTERLOCK_OR_TIMEOUT"


def _drive(controller: McController, clock: SimClock, line: str, at_ms: int) -> list[str]:
    clock.advance(max(0, at_ms - clock.now_ms()))
    controller.run_until(clock.now_ms())
    controller.receive((line + "\n").encode("ascii"))
    controller.run_until(clock.now_ms())
    for _ in range(1000):
        if not controller.has_pending_input():
            break
        controller.poll()
    else:
        raise AssertionError("RX 队列未清空")
    return [text for _t, text in controller.take_events()]


def _run(scenario_id: str):
    scenario = BUILTIN_SCENARIOS[scenario_id]
    controller, clock = scenario.build_controller()
    transcript = scenario.run(controller, clock)
    mcu = [row["line"] for row in transcript if row["dir"] == "mcu"]
    return controller, clock, mcu


def _booted(pwm_ready: bool = True):
    clock = SimClock()
    controller = McController(clock, pwm_ready=pwm_ready)
    controller.power_on()
    controller.take_events()  # 丢弃启动行（READY/FAULT），便于按调用取增量输出
    return controller, clock


# ---------------------------------------------------------------- 场景矩阵
@pytest.mark.parametrize(
    "scenario_id,expected",
    [
        ("interlock_dock_open", "ERR INTERLOCK"),
        ("interlock_estop", "ERR INTERLOCK"),
        ("interlock_role_mismatch", "ERR INTERLOCK"),
        ("already_armed", "ERR ALREADY_ARMED"),
        ("frame_out_of_range", "ERR FRAME"),
        ("frame_seq_regress", "ERR FRAME"),
        ("frame_unarmed", "ERR FRAME"),
        ("watchdog_timeout", FAULT),
        ("unplug_during_motion", FAULT),
        ("estop_during_motion", FAULT),
        ("swap_role_during_motion", FAULT),
        ("line_overflow", "ERR LENGTH"),
        ("unknown_command", "ERR COMMAND"),
    ],
)
def test_scenario_emits_expected_fault_line(scenario_id, expected):
    _controller, _clock, mcu = _run(scenario_id)
    assert expected in mcu, f"{scenario_id}: 期望 {expected}，实际 {mcu}"


def test_interlock_unstable_then_ok_after_stable_window():
    _controller, _clock, mcu = _run("interlock_unstable")
    assert mcu.index("ERR INTERLOCK") < mcu.index("OK ARM 1")


def test_pwm_init_fail_reports_on_boot():
    _controller, _clock, mcu = _run("pwm_init_fail")
    assert mcu[0] == "FAULT PWM_INIT"


def test_invalid_frame_stops_pwm():
    controller, _clock, mcu = _run("frame_out_of_range")
    assert "ERR FRAME" in mcu
    assert controller.armed is False
    assert controller.duty == [0, 0, 0, 0]
    assert not any("SETTLED" in line for line in mcu)


# ---------------------------------------------------------------- 固件语义细节
def test_stable_window_boundary_200ms():
    controller, clock = _booted()
    at_200 = _drive(controller, clock, "ARM 1", 200)
    assert "ERR INTERLOCK" in at_200
    assert not any(line.startswith("OK ARM") for line in at_200)
    at_201 = _drive(controller, clock, "ARM 1", 201)
    assert "OK ARM 1" in at_201


def test_arm_sscanf_trailing_whitespace_is_accepted():
    """sscanf("ARM %d %c")==1：**尾随空白被接受**（%c 前的空白指令吃掉它后无字符可读）。

    本条此前断言 ``"ARM 1 "`` 应 ``ERR COMMAND``，那是在**保护错误行为**：
    真实 C 运行时对该输入返回 1，真机因此会 ARM 成功。模拟器若比真机更严，
    联调期会给出与真机相反的结论。判定基准见 tests/unit/test_review_wire_parity.py
    （以纯 Python 复刻 sscanf 赋值计数为准，不拿模拟器自己当 oracle）。

    三个方向的基准值：尾随 token -> %c 成功 -> sscanf 返回 2 -> 拒绝；
    尾随空白 -> %c 失败但 %d 已赋值 -> sscanf 返回 1 -> **接受**；
    「ARM1」-> 格式串空白匹配零个 -> 仍返回 1 -> 接受。
    """
    controller, clock = _booted()
    assert "ERR COMMAND" in _drive(controller, clock, "ARM 1 2", 300)
    assert "OK ARM 1" in _drive(controller, clock, "ARM 1 ", 320)

    # 「ARM1」需用独立控制器：上一条已 ARM 成功，再 ARM 会命中 ERR ALREADY_ARMED
    other, other_clock = _booted()
    assert "OK ARM 1" in _drive(other, other_clock, "ARM1", 340)       # 格式串空白匹配零个


def test_previous_seq_is_not_reset_by_stop_but_rearm_resets_via_have_seq():
    controller, clock = _booted()
    assert "OK ARM 1" in _drive(controller, clock, "ARM 1", 300)
    assert _drive(controller, clock, "FRAME 5 1500 1550 1500 1500", 320) == []
    assert "ERR FRAME" in _drive(controller, clock, "FRAME 4 1500 1550 1500 1500", 340)
    controller.stop_motion()
    assert controller.previous_seq == 5, "previousSeq 不因 stopMotion 重置（有意的重放防护）"
    assert "OK ARM 1" in _drive(controller, clock, "ARM 1", 400)
    assert _drive(controller, clock, "FRAME 0 1500 1550 1500 1500", 420) == []


def test_status_does_not_feed_watchdog():
    controller, clock = _booted()
    assert "OK ARM 1" in _drive(controller, clock, "ARM 1", 300)
    _drive(controller, clock, "FRAME 0 1500 1550 1500 1500", 320)
    emitted: list[str] = []
    for moment in range(340, 700, 20):
        emitted += _drive(controller, clock, "STATUS", moment)
    assert any(line.startswith("STATE ") for line in emitted), "STATUS 应一直产出状态上报"
    assert FAULT in emitted, "单纯 STATUS 不能刷新看门狗，必须停机"
    assert controller.armed is False


def test_watchdog_fires_strictly_after_300ms():
    controller, clock = _booted()
    _drive(controller, clock, "ARM 1", 300)
    _drive(controller, clock, "FRAME 0 1500 1550 1500 1500", 320)
    clock.advance(300)  # now = 620, 620-320 == 300 → 不触发
    controller.run_until(clock.now_ms())
    assert not any(text == FAULT for _t, text in controller.take_events())
    clock.advance(1)  # 321 > 300 → 触发
    controller.run_until(clock.now_ms())
    assert any(text == FAULT for _t, text in controller.take_events())


def test_pwm_step_is_limited_to_six_microseconds_per_20ms():
    controller, clock = _booted()
    _drive(controller, clock, "ARM 1", 300)
    _drive(controller, clock, "FRAME 0 1600 1600 1600 1600", 320)
    previous = list(controller.current_us)
    for _ in range(340, 1200, 20):
        clock.advance(20)
        controller.run_until(clock.now_ms())
        for axis, (before, after) in enumerate(
            zip(previous, controller.current_us, strict=True)
        ):
            assert abs(after - before) <= 6, f"轴{axis} 单步超过 6us"
        previous = list(controller.current_us)
    # 100us 行程 / 6us 每 20ms => 峰值速度约 300us/s
    assert controller.current_us[0] > 1500


def test_serial_budget_limits_bytes_per_cycle():
    controller, clock = _booted()
    clock.advance(300)
    controller.run_until(300)
    payload = ("FRAME 0 1500 1550 1500 1500\n" * 8).encode("ascii")
    assert len(payload) > 64
    controller.receive(payload)
    controller.loop_once(300)
    # 单圈最多 64 字节：剩余必须仍在 RX 中了（未被一次性吃光）
    assert controller.has_pending_input()


def test_flood_does_not_block_safety_check():
    controller, clock = _booted()
    _drive(controller, clock, "ARM 1", 300)
    controller.receive(("FRAME 0 1500 1550 1500 1500\n" * 200).encode("ascii"))
    controller.estop.pressed = True
    controller.loop_once(320)
    emitted = [text for _t, text in controller.take_events()]
    assert FAULT in emitted, "通信洪泛期间急停仍必须在同一圈内触发"


def test_button_debounce_and_edge_emission():
    controller, clock = _booted()
    controller.buttons.press(1)
    clock.advance(20)
    controller.run_until(clock.now_ms())
    assert [t for _t, t in controller.take_events()] == []
    clock.advance(15)  # 累计 35ms > 30ms 去抖
    controller.run_until(clock.now_ms())
    assert "BUTTON 1" in [t for _t, t in controller.take_events()]


def test_settled_reports_only_when_converged():
    controller, clock = _booted()
    _drive(controller, clock, "ARM 1", 300)
    _drive(controller, clock, "FRAME 0 1500 1550 1500 1500", 320)
    clock.advance(400)
    controller.run_until(clock.now_ms())
    lines = [text for _t, text in controller.take_events()]
    assert any(line.startswith("SETTLED ") for line in lines)
    assert any(line == "STATE 1 1 1 1" for line in lines)
