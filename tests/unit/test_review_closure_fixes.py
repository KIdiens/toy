"""复查整改（第一批）回归护栏：核心闭环与命令代数。

每条都源自一次**已复现**的缺陷：先复现失败、再修、在此钉住。覆盖：

- 物理表演键卡在 PERFORMING（`pump_once` 的 `motion_busy` 守卫误丢按键命令）
- RECORDING 无上限（无硬件档位按一次对话键后永久停录）
- 取消录音凭空多录一段（EXIT 命令携带**迁移后**代数）
- 被拒绝的 `play_action` 有副作用（409 却已经改了状态机）
- 动作文件非法落 SAFETY_TRIP 而非 409
"""
from __future__ import annotations

import contextlib
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from host.adapters.queues import CommandRouter
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.bus import EventBus
from host.core.engine import Engine
from host.core.errors import ConflictError
from host.core.events import CommandKind
from host.core.state_machine import SessionStateMachine, State, Trigger

from tests.support import TOY_ROOT


def _context(tmp_path: Path, name: str = "toy.sqlite3", *, root: Path | None = None):
    settings = load_settings({"DB_PATH": str(tmp_path / name), "MOTION_BOOT_WAIT_S": "0.05"})
    return build_context(settings, simulate=True, root=root or TOY_ROOT)


@contextlib.contextmanager
def _running(context):
    """完整装配：`start_workers()` 只管 net/audio/tick，motion worker 与 main.py 一样单独起。

    漏掉 motion worker 会让运动命令永远无人消费——那会让本例的断言全部失真。
    """
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


def _await_state(context, target: str, timeout_s: float = 3.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if context.state_machine.state.value == target:
            return target
        time.sleep(0.02)
    return context.state_machine.state.value


# --------------------------------------------------------------- 表演键闭环
def test_perform_button_returns_to_idle(tmp_path: Path) -> None:
    """物理表演键必须能收尾回 IDLE（此前永久卡 PERFORMING）。"""
    context = _context(tmp_path, "perform.sqlite3")
    with _running(context):
        assert context.state_machine.state is State.IDLE
        context.press_button(2)
        assert context.state_machine.state is State.PERFORMING

        final = _await_state(context, "IDLE")
        history = [(r.source.value, r.trigger.value, r.dest.value)
                   for r in context.state_machine.history]
        assert final == "IDLE", f"表演键未收尾，仍停在 {final}（迁移历史 {history}）"
        assert ("PERFORMING", "PERFORM_DONE", "IDLE") in history, history


# --------------------------------------------------------------- RECORDING 上限
def test_recording_has_a_bounded_timeout(tmp_path: Path) -> None:
    """RECORDING 必须有上限兜底：无硬件档位录不到 15s 上限，此前会永久停录。"""
    context = _context(tmp_path, "record.sqlite3")
    context._record_limit_s = lambda: 0.2          # 缩短上限以便快速验证兜底路径
    with _running(context):
        context.press_button(1)
        assert context.state_machine.state is State.RECORDING

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if context.state_machine.state is not State.RECORDING:
                break
            time.sleep(0.02)
        assert context.state_machine.state is not State.RECORDING, (
            "RECORDING 未在兜底上限内自动结束——设备会永久停在录音态"
        )
        assert ("RECORDING", "REC_STOP", "THINKING") in [
            (r.source.value, r.trigger.value, r.dest.value)
            for r in context.state_machine.history
        ]


# --------------------------------------------------------------- EXIT 代数
def test_exit_command_carries_source_generation() -> None:
    """EXIT 命令必须带**迁移前**代数，否则下游会误判「录音没就绪」而多录一段。"""
    machine = SessionStateMachine(initial=State.IDLE, generation=5)
    engine = Engine(EventBus(), CommandRouter(), state_machine=machine)

    started = [c for c in engine.apply(Trigger.DIALOG_PRESS)
               if c.kind is CommandKind.RECORD_START]
    assert started and started[0].generation == 5

    stopped = [c for c in engine.apply(Trigger.REC_CANCEL)
               if c.kind is CommandKind.RECORD_STOP]
    assert stopped, "取消录音必须下发 RECORD_STOP"
    assert stopped[0].generation == 5, "EXIT 命令携带了迁移后的代数"
    # 关键：确认代数**确实**在进入 IDLE 时变了，否则上一条断言是空测
    assert machine.generation == 6, "进入 IDLE 应当已 +1"


def test_cancelling_recording_does_not_start_another_one(tmp_path: Path) -> None:
    """取消录音后再多录一段，是 EXIT 代数错位在真机档位下的实际后果。"""
    context = _context(tmp_path, "cancel.sqlite3")
    calls: list[int] = []
    original = context.recorder.record

    def spy(*args, **kwargs):
        calls.append(context.state_machine.generation)
        return original(*args, **kwargs)

    context.recorder.record = spy
    with _running(context):
        context.press_button(1)              # IDLE -> RECORDING
        assert _await_state(context, "RECORDING") == "RECORDING"
        time.sleep(0.3)
        before = len(calls)
        assert before >= 1, "录音根本没有启动，本例失去意义"

        context.press_button(2)              # RECORDING --REC_CANCEL--> IDLE
        assert _await_state(context, "IDLE") == "IDLE"
        time.sleep(0.4)
        assert len(calls) == before, (
            f"取消录音后又多录了 {len(calls) - before} 段（调用发生的代数 {calls}）"
        )


# --------------------------------------------------------------- 409 副作用
def test_play_action_while_speaking_is_rejected_without_side_effect(tmp_path: Path) -> None:
    """被拒绝的播放请求不得改动状态机（此前 409 已经把 SPEAKING 切成了 IDLE）。"""
    context = _context(tmp_path, "speaking.sqlite3")
    try:
        context.press_button(1)
        context.press_button(1)              # -> THINKING
        context.engine.apply(Trigger.AI_DONE)
        context.drain()
        assert context.state_machine.state is State.SPEAKING, "前置条件未构造成功"

        with pytest.raises(ConflictError):
            context.play_action(role_id=1, action_id="wave")
        assert context.state_machine.state is State.SPEAKING, "被拒绝的请求改动了状态机"
    finally:
        context.close()


def test_invalid_action_file_is_rejected_at_the_door(tmp_path: Path) -> None:
    """非法动作文件应在入口 409，而不是异步落进 SAFETY_TRIP → 永久故障锁定。"""
    root = tmp_path / "root"
    shutil.copytree(TOY_ROOT / "content", root / "content")
    (root / "content" / "roles" / "1" / "actions" / "wave.json").write_text(
        json.dumps({}), encoding="utf-8")

    context = _context(tmp_path, "badaction.sqlite3", root=root)
    try:
        with pytest.raises(ConflictError):
            context.play_action(role_id=1, action_id="wave")
        assert context.state_machine.state is not State.FAULT_LOCKED
        assert context.fault is None
    finally:
        context.close()
