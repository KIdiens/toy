"""物理表演键 = 打招呼：动作真的发出、收尾回 IDLE、连按与打断均不卡死。

回归的真实缺陷：表演键按下后 ``PLAY_ACTION`` 带着**空载荷**下发，
``execution.pump_once`` 走 ``_finish_without_motion`` 收尾，于是
「按下去一个动作都不做，只走一趟 IDLE → PERFORMING → IDLE」。

本文件钉住的是**端到端可观测行为**（动作真发 + 状态真收敛），不是内部调用：
``test_review_closure_fixes.test_perform_button_returns_to_idle`` 早就在覆盖
「能回 IDLE」，但它不断言动作真的播了 —— 缺陷正是从这道缝里漏过去的。
"""
from __future__ import annotations

import contextlib
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.state_machine import State

from tests.support import TOY_ROOT

# 固件要求角色稳定 STABLE_MS=200ms 才允许 ARM（sim/controller.py），
# 而 MotionClient 播前只推进 boot_wait_s。这里给足稳定期，否则 ARM 前的
# 到位/角色检查必然失败——那是**装置问题**，不是被测行为。
BOOT_WAIT_S = "2.0"


def _context(tmp_path: Path, name: str = "greet.sqlite3", *, root: Path | None = None):
    settings = load_settings({
        "DB_PATH": str(tmp_path / name),
        "MOTION_BOOT_WAIT_S": BOOT_WAIT_S,
    })
    return build_context(settings, simulate=True, root=root or TOY_ROOT)


@contextlib.contextmanager
def _running(context, *, spy: list | None = None):
    """完整装配并起 motion worker；同时把 ``motion.play`` 的真实调用记进 ``spy``。"""
    if spy is not None:
        original = context.motion.play

        def record(action, *, role_id=None):
            spy.append({"name": action.get("name"), "role_id": role_id,
                        "file_role_id": action.get("role_id"),
                        "end_ms": action["frames"][-1]["t_ms"]})
            return original(action, role_id=role_id)

        context.motion.play = record

    stop = threading.Event()
    context.worker_running = True
    context.start_workers()
    thread = threading.Thread(target=context.run_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        yield context
    finally:
        stop.set()
        context.close()


def _await_state(context, target: str, timeout_s: float = 8.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if context.state_machine.state.value == target:
            return target
        time.sleep(0.02)
    return context.state_machine.state.value


def _history(context):
    return [(r.source.value, r.trigger.value, r.dest.value)
            for r in context.state_machine.history]


# --------------------------------------------------------------- 动作真的发出
def test_perform_button_actually_plays_the_greet_action(tmp_path: Path) -> None:
    """表演键必须真的让硬件动起来：``motion.play`` 被调用且拿到非空动作载荷。

    这是缺陷的正面对照：修好前 ``motion.play`` 是**零调用**，状态白走一趟。
    """
    context = _context(tmp_path, "play.sqlite3")
    played: list[dict] = []
    with _running(context, spy=played):
        assert context.state_machine.state is State.IDLE
        context.press_button(2)
        assert context.state_machine.state is State.PERFORMING

        assert _await_state(context, "IDLE") == "IDLE"
        time.sleep(0.3)

        assert played, "表演键没有播出任何动作（motion.play 零调用）——缺陷仍在"
        assert played[0]["name"] == "打招呼", played
        assert played[0]["end_ms"] > 0, "动作时间轴为空"
        # 动作自带的 role_id 与下发给 MotionClient 的 role_id 必须一致，
        # 否则 ARM 前的角色检查会拿另一套限位去比。
        assert played[0]["role_id"] == played[0]["file_role_id"] == 1, played
        assert context.action_progress["action_id"] == "greet", context.action_progress
        assert context.fault is None, context.fault


def test_greet_action_exists_for_every_role(tmp_path: Path) -> None:
    """三个角色都必须自带 ``greet``——按键行为不能随角色包内容漂移。

    依据：``role.json`` 的白名单里 wave 只有 role 1/2、nod 只有 role 1/3，
    没有任何现有 id 被三个角色共享，所以表演键才需要独立的 greet。
    """
    from host.motion.validation import validate

    for role_id in (1, 2, 3):
        context = _context(tmp_path, f"role{role_id}.sqlite3")
        try:
            assert "greet" in context.roles.actions_for(role_id), (
                f"角色 {role_id} 的白名单里没有 greet：{context.roles.actions_for(role_id)}"
            )
            path = context.roles.action_path(role_id, "greet")
            assert path is not None, f"角色 {role_id} 解析不到 greet.json"
            action = json.loads(Path(path).read_text(encoding="utf-8"))
            validate(action)          # 单一真源：非法会抛 ValueError
            assert action["role_id"] == role_id, action["role_id"]
            # 末帧回中位由 validate 保证；这里再钉住「时长不会长到咬住超时」：
            # TAIL_TIMEOUT 只管末帧之后，不管时间轴本身，但时间轴越长
            # motion worker 被同步占住越久，对话键打断就越晚。
            assert action["frames"][-1]["t_ms"] <= 5000, action["frames"][-1]["t_ms"]
        finally:
            context.close()


def test_greet_stays_inside_each_role_limits(tmp_path: Path) -> None:
    """``greet`` 的幅值必须落在各角色自己的 ``min_us``/``max_us`` 内。

    ``host/motion/validation.py`` 只卡 1400~1600 的固件硬限，**不**校验角色限幅
    （限幅只声明在 ``role.json``），所以这条只能在这里钉住。
    """
    from host.motion.validation import validate

    for role_id in (1, 2, 3):
        context = _context(tmp_path, f"limits{role_id}.sqlite3")
        try:
            config = context.roles.get(role_id)
            action = json.loads(
                context.roles.action_path(role_id, "greet").read_text(encoding="utf-8")
            )
            validate(action)
            for frame in action["frames"]:
                for axis, value in zip(config.axes, frame["us"], strict=True):
                    assert config.min_us <= value <= config.max_us, (
                        f"角色 {role_id} 的 {axis} 越限：{value} 不在 "
                        f"[{config.min_us}, {config.max_us}]"
                    )
        finally:
            context.close()


# --------------------------------------------------------------- 收尾不卡死
def test_perform_button_returns_to_idle_without_sticking(tmp_path: Path) -> None:
    """动作播完后必须回 IDLE，不能停在 PERFORMING。

    这正是 ``motion_busy`` 陷阱的落点：按键路径没有 ``ports.play_action`` 那种
    「入队即置忙」的入口，注入动作载荷后若不置 ``motion_busy``，``pump_once``
    的守卫会把它当陈旧命令丢弃 → ``PERFORM_DONE`` 永不产生 → 永久卡 PERFORMING。
    """
    context = _context(tmp_path, "settle.sqlite3")
    played: list[dict] = []
    with _running(context, spy=played):
        context.press_button(2)
        final = _await_state(context, "IDLE")
        history = _history(context)

        assert final == "IDLE", f"未收尾，仍停在 {final}（迁移历史 {history}）"
        assert ("PERFORMING", "PERFORM_DONE", "IDLE") in history, history
        assert played, "没有实际播出动作，收尾只是空转"


def test_role_switch_then_perform_button_still_plays(tmp_path: Path) -> None:
    """换到 role 2 / role 3 后按表演键，仍要播 greet 并回 IDLE。

    ``switch_active_role`` 只改注册表；MCU 模拟器的角色电阻硬编码为 role=1
    （``runtime._make_sim``），真机上换角色＝重新插公仔、电阻自然变化，
    所以这里必须手动同步电阻，否则 ARM 前的角色检查必然 INTERLOCK——
    那是装置问题，不是被测行为。
    """
    for role_id in (2, 3):
        context = _context(tmp_path, f"switch{role_id}.sqlite3")
        played: list[dict] = []
        with _running(context, spy=played):
            context.switch_active_role(role_id)
            context.sim.role_res.role = role_id
            time.sleep(0.3)

            context.press_button(2)
            final = _await_state(context, "IDLE")
            time.sleep(0.3)

            assert final == "IDLE", f"角色 {role_id} 按键未收尾，停在 {final}"
            assert context.fault is None, f"角色 {role_id} 出现故障：{context.fault}"
            assert played and played[0]["role_id"] == role_id, (
                f"角色 {role_id} 没有播出自己的 greet：{played}"
            )


def test_missing_greet_degrades_without_sticking_or_faulting(tmp_path: Path) -> None:
    """角色包缺 ``greet.json`` 时必须安静降级：不动硬件、回 IDLE、**不进故障锁定**。

    按键路径没有 409 入口，动作包内容问题若冒泡成异常，会被 ``_run_play``
    归类为 SAFETY_TRIP → 设备永久故障锁定（表现成「玩具坏了」），而实际只是内容缺失。
    """
    root = tmp_path / "root"
    shutil.copytree(TOY_ROOT / "content", root / "content")
    (root / "content" / "roles" / "1" / "actions" / "greet.json").unlink()

    context = _context(tmp_path, "nogreet.sqlite3", root=root)
    played: list[dict] = []
    with _running(context, spy=played):
        context.press_button(2)
        final = _await_state(context, "IDLE")
        time.sleep(0.3)

        assert final == "IDLE", f"缺动作包时卡在 {final}，设备会永久停机"
        assert not played, "缺动作包却仍尝试驱动硬件"
        assert context.fault is None, f"缺动作包不该进故障锁定：{context.fault}"
        assert context.worker_faults == 0, context.worker_faults


def test_invalid_greet_file_does_not_lock_the_device(tmp_path: Path) -> None:
    """``greet.json`` 内容非法时同样只降级，不得 SAFETY_TRIP。"""
    root = tmp_path / "root"
    shutil.copytree(TOY_ROOT / "content", root / "content")
    (root / "content" / "roles" / "1" / "actions" / "greet.json").write_text(
        json.dumps({"schema": 1, "role_id": 1, "frames": []}), encoding="utf-8")

    context = _context(tmp_path, "badgreet.sqlite3", root=root)
    with _running(context):
        context.press_button(2)
        final = _await_state(context, "IDLE")
        time.sleep(0.3)

        assert final == "IDLE", f"非法动作文件导致卡在 {final}"
        assert context.state_machine.state is not State.FAULT_LOCKED
        assert context.fault is None, f"内容非法不该进故障锁定：{context.fault}"


# --------------------------------------------------------------- 连按 / 打断
def test_pressing_perform_button_twice_ends_idle(tmp_path: Path) -> None:
    """连按两次表演键：第二次是取消（PERFORMING → IDLE），不得卡死。"""
    context = _context(tmp_path, "twice.sqlite3")
    with _running(context):
        context.press_button(2)
        time.sleep(0.05)
        context.press_button(2)          # 播放中再按一下 = 取消

        final = _await_state(context, "IDLE")
        time.sleep(0.6)
        history = _history(context)

        assert final == "IDLE", f"连按两次后停在 {final}（迁移历史 {history}）"
        assert ("PERFORMING", "PERFORM_PRESS", "IDLE") in history, history
        assert context.fault is None, context.fault


def test_dialog_button_interrupts_the_greet_and_starts_recording(tmp_path: Path) -> None:
    """对话键必须能打断正在打招呼的动作并进入录音，不能卡在 PERFORMING。"""
    context = _context(tmp_path, "interrupt.sqlite3")
    played: list[dict] = []
    with _running(context, spy=played):
        context.press_button(2)
        time.sleep(0.3)                   # greet 时间轴 1600ms，此刻正在播
        assert context.state_machine.state is State.PERFORMING
        assert context.motion_busy, "前提不成立：动作根本没在途，打断无从测起"

        context.press_button(1)
        assert context.state_machine.state is State.RECORDING, (
            f"对话键未能打断打招呼，仍停在 {context.state_machine.state.value}"
        )
        assert ("PERFORMING", "DIALOG_PRESS", "RECORDING") in _history(context)
        # 打断后不得有故障，也不得因为动作收尾而把状态拽回去
        time.sleep(1.0)
        assert context.fault is None, context.fault
        assert context.state_machine.state is State.RECORDING, (
            f"打断后状态被动作收尾拽走：{context.state_machine.state.value}"
        )


# --------------------------------------------------------------- 反向验证护栏
@pytest.mark.parametrize("state", [State.IDLE, State.THINKING])
def test_perform_press_always_marks_the_greet_intent(state: State) -> None:
    """凡是表演键导致进入 PERFORMING，``PLAY_ACTION`` 必须带 greet 语义标记。

    没有标记，adapter 就无从知道该播哪个动作 —— 缺陷会以「按了没反应」的形式回来。
    """
    from host.adapters.queues import CommandRouter
    from host.core.bus import EventBus
    from host.core.engine import GREET_INTENT, Engine
    from host.core.events import CommandKind, Event, EventKind
    from host.core.state_machine import SessionStateMachine

    machine = SessionStateMachine(initial=state, generation=2)
    engine = Engine(EventBus(), CommandRouter(), state_machine=machine)
    # 直接派发按键事件，覆盖 default_dispatcher 的 BUTTON 分支
    commands = engine._dispatcher(
        engine, Event(kind=EventKind.BUTTON, payload={"n": 2}, generation=2))
    plays = [c for c in commands if c.kind is CommandKind.PLAY_ACTION]

    assert machine.state is State.PERFORMING
    assert plays, "进入 PERFORMING 却没有 PLAY_ACTION 命令"
    assert plays[0].payload.get("perform_intent") == GREET_INTENT, plays[0].payload
