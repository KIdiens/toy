"""P0：停止后立刻再播 / ``replace_current`` 不得把新动作丢成永久 PERFORMING。

缺陷形态（真实进程复现）：A 在播 → 播 B → A 播完发现代数不符，走 ``_run_play`` 的
陈旧分支把**共享布尔** ``motion_busy`` 清成 False（那是 B 的忙标志）→ worker 取到 B
时看到 False，当陈旧命令丢弃且**不发 PERFORM_DONE** → 状态机无出口，永久卡
PERFORMING，只有再按一次表演键才能回 IDLE。

修法：把「陈旧判定」从共享布尔换成**每次播放的令牌**（``play_seq``）——
``stop`` 与「被新播放替换」都会让在途令牌失效，而**旧播放结束只清自己的令牌**，
绝不会误清更新那次播放的令牌。

本文件全部走真实队列 + 真实 ``run_forever`` worker + 真实状态机，只把**硬件播放**
（``motion.play``）换成可控耗时的替身 —— 那是 I/O 边界，不是被测对象。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.events import EventKind
from host.core.state_machine import State
from host.motion.client import PlayResult

from tests.support import TOY_ROOT

PLAY_S = 0.35          # 替身动作耗时：足够在「播放中」插入第二条命令
SETTLE_TIMEOUT_S = 6.0


class _SlowMotion:
    """可观测、可控耗时的 ``motion.play`` 替身（真实 worker / 真实队列不变）。"""

    def __init__(self, duration_s: float = PLAY_S) -> None:
        self.duration_s = duration_s
        self.calls: list[dict] = []
        self.entered = threading.Event()

    def play(self, action, *, role_id=None, min_us=1400, max_us=1600, cancel=None):
        self.calls.append({
            "name": action.get("name"),
            "role_id": role_id,
            "file_role_id": action.get("role_id"),
            "min_us": min_us,
            "max_us": max_us,
            "end_ms": action["frames"][-1]["t_ms"],
        })
        self.entered.set()
        time.sleep(self.duration_s)
        return PlayResult(role_id=int(role_id or 0), frames_sent=len(action["frames"]),
                          final_seq=len(action["frames"]) - 1, hold_ms=150,
                          end_ms=int(action["frames"][-1]["t_ms"]))


def _context(tmp_path: Path, name: str = "token.sqlite3"):
    settings = load_settings({"DB_PATH": str(tmp_path / name)})
    return build_context(settings, simulate=True, root=TOY_ROOT)


class _Rig:
    """真实装配：worker 线程 + 状态机；``close`` 里一定停线程（不留后台进程）。"""

    def __init__(self, tmp_path: Path, name: str = "token.sqlite3") -> None:
        self.context = _context(tmp_path, name)
        self.motion = _SlowMotion()
        self.context.motion = self.motion
        self.events: list[EventKind] = []
        original_publish = self.context.bus.publish

        def record(event):
            self.events.append(event.kind)
            return original_publish(event)

        self.context.bus.publish = record
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self) -> _Rig:
        self.context.worker_running = True
        self.context.start_workers()
        self.thread = threading.Thread(
            target=self.context.run_forever, args=(self.stop,), daemon=True,
            name="test-motion-worker")
        self.thread.start()
        time.sleep(0.15)  # 等 worker 进入循环（与既有测试同一节奏）
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3.0)
        self.context.close()

    # ---- 观测 ----
    def await_state(self, target: str, timeout_s: float = SETTLE_TIMEOUT_S) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.context.state_machine.state.value == target:
                return target
            time.sleep(0.02)
        return self.context.state_machine.state.value

    def history(self) -> list[tuple[str, str, str]]:
        return [(r.source.value, r.trigger.value, r.dest.value)
                for r in self.context.state_machine.history]


def _wait_in_flight(rig: _Rig) -> None:
    assert rig.motion.entered.wait(3.0), "前置条件不成立：动作没有真正开始播放"
    assert rig.context.state_machine.state is State.PERFORMING


# ---------------------------------------------------------------- 缺陷主场景
def test_stop_then_immediate_play_really_plays_and_settles(tmp_path: Path) -> None:
    """停止后立刻再播：第二条必须**真的播**，且最终回 IDLE。

    判别力：回退令牌修复（陈旧分支无条件清忙标志）→ 第二条被当陈旧丢弃 →
    状态永久停在 PERFORMING，本测试转红。
    """
    with _Rig(tmp_path, "stopplay.sqlite3") as rig:
        ctx = rig.context
        ctx.play_action(role_id=1, action_id="wave")
        _wait_in_flight(rig)
        assert ctx.motion_busy is True

        ctx.stop_action()
        assert ctx.state_machine.state is State.IDLE
        assert ctx.motion_busy is False

        ctx.play_action(role_id=1, action_id="nod")
        final = rig.await_state("IDLE")

        assert final == "IDLE", (
            f"停止后再播未收尾，停在 {final}（历史 {rig.history()}）")
        assert [c["name"] for c in rig.motion.calls] == ["小幅度挥手", "点头"], (
            f"第二条动作没有真正下发：{rig.motion.calls}")
        assert ctx.fault is None, ctx.fault


def test_replace_current_really_plays_the_replacement(tmp_path: Path) -> None:
    """播放中 ``replace_current=True``：替换动作必须真的播，且最终回 IDLE。"""
    with _Rig(tmp_path, "replace.sqlite3") as rig:
        ctx = rig.context
        ctx.play_action(role_id=1, action_id="wave")
        _wait_in_flight(rig)

        ctx.play_action(role_id=1, action_id="nod", replace_current=True)
        final = rig.await_state("IDLE")

        assert final == "IDLE", (
            f"replace_current 未收尾，停在 {final}（历史 {rig.history()}）")
        assert [c["name"] for c in rig.motion.calls] == ["小幅度挥手", "点头"], (
            f"替换动作没有真正下发：{rig.motion.calls}")
        assert ctx.fault is None, ctx.fault


# ---------------------------------------------------------------- 必须保留的语义
def test_stop_without_new_play_starts_nothing_afterwards(tmp_path: Path) -> None:
    """只停不接播：停之后不得再有任何动作下发，也不得再产出 PERFORM_DONE。"""
    with _Rig(tmp_path, "stopsolo.sqlite3") as rig:
        ctx = rig.context
        ctx.play_action(role_id=1, action_id="wave")
        _wait_in_flight(rig)

        ctx.stop_action()
        mark = len(ctx.state_machine.history)
        time.sleep(PLAY_S + 0.6)      # 让在途旧动作彻底结束

        assert len(rig.motion.calls) == 1, f"停止后又播了动作：{rig.motion.calls}"
        assert ctx.state_machine.state is State.IDLE
        assert len(ctx.state_machine.history) == mark, (
            f"停止后状态机仍被旧动作推进：{rig.history()}")
        assert EventKind.PERFORM_DONE not in rig.events, (
            "停止后仍投出了 PERFORM_DONE")
        assert ctx.action_progress is None, f"停止后仍残留进度：{ctx.action_progress}"


def test_role_switch_after_stop_discards_the_old_action(tmp_path: Path) -> None:
    """换角（代数变）后旧动作不得继续、不得把 PERFORM_DONE 投到新一代。

    旧动作播完走陈旧分支；随后按表演键必须播**新角色自己的** greet 并回 IDLE
    （证明陈旧分支没有把新一代的忙标志/状态弄坏）。
    """
    with _Rig(tmp_path, "rolechange.sqlite3") as rig:
        ctx = rig.context
        ctx.play_action(role_id=1, action_id="wave")
        _wait_in_flight(rig)

        ctx.stop_action()
        ctx.switch_active_role(2)
        ctx.sim.role_res.role = 2
        generation_after_switch = ctx.state_machine.generation
        rig.events.clear()
        time.sleep(PLAY_S + 0.6)      # 旧动作在这一窗口内播完

        assert ctx.state_machine.state is State.IDLE, (
            f"旧动作把新一代状态拽走了：{ctx.state_machine.state.value}")
        assert EventKind.PERFORM_DONE not in rig.events, (
            f"旧动作把 PERFORM_DONE 投进了新会话：{rig.events}")
        assert ctx.state_machine.generation == generation_after_switch

        ctx.press_button(2)
        final = rig.await_state("IDLE")
        assert final == "IDLE", f"换角后按键卡在 {final}（历史 {rig.history()}）"
        assert rig.motion.calls[-1]["role_id"] == 2, (
            f"换角后没有播新角色的动作：{rig.motion.calls}")


def test_stale_greet_without_action_does_not_publish_perform_done(
        tmp_path: Path) -> None:
    """上一代的「表演键但没有动作」命令不得投 PERFORM_DONE。

    判别力：``PERFORMING --PERFORM_DONE--> IDLE`` 是合法迁移。若这条迟到收尾事件
    被投进**新会话**，就会把新会话正在表演的动作直接拽回 IDLE。旧实现只靠调用方
    在 ``pump_once`` 里预检一次代数，收尾函数自己不复核。
    """
    import json
    import shutil

    from host.core.events import Command, CommandKind

    root = tmp_path / "root"
    shutil.copytree(TOY_ROOT / "content", root / "content")
    (root / "content" / "roles" / "1" / "actions" / "greet.json").write_text(
        json.dumps({"schema": 1, "role_id": 1, "frames": []}), encoding="utf-8")

    context = build_context(
        load_settings({"DB_PATH": str(tmp_path / "stale.sqlite3")}),
        simulate=True, root=root)
    events: list[EventKind] = []
    original_publish = context.bus.publish

    def record(event):
        events.append(event.kind)
        return original_publish(event)

    context.bus.publish = record
    try:
        # 上电会在 work 队列里留下 NOTIFY 等命令：不排空的话 pump_once 消费的是它们，
        # 本条会「什么都没验到」地通过（假绿）。
        context.drain()
        assert context.work.qsize() == 0
        stale_generation = context.state_machine.generation - 1
        context._router.put(Command(
            kind=CommandKind.PLAY_ACTION,
            payload={"perform_intent": "greet"},
            generation=stale_generation))
        context.pump_once()

        assert EventKind.PERFORM_DONE not in events, (
            f"上一代的无动作收尾投出了 PERFORM_DONE：{events}")
        assert context.state_machine.state is State.IDLE
    finally:
        context.close()


def test_single_thread_mode_unchanged(tmp_path: Path) -> None:
    """单线程档位（``worker_running=False``）：串行播放与停止清队列行为不变。"""
    ctx = _context(tmp_path, "single.sqlite3")
    motion = _SlowMotion(duration_s=0.0)
    ctx.motion = motion
    try:
        assert ctx.motion_busy is False
        assert ctx.play_action(role_id=1, action_id="wave")["scheduled"] is True
        ctx.drain()
        assert ctx.state_machine.state is State.IDLE
        assert ctx.motion_busy is False
        assert len(motion.calls) == 1

        # 排入后立刻停止：队列必须清空，drain 不得再播
        ctx.play_action(role_id=1, action_id="wave")
        ctx.stop_action()
        assert ctx.work.qsize() == 0
        ctx.drain()
        assert len(motion.calls) == 1, f"停止后队列残留仍被播放：{motion.calls}"
        assert ctx.state_machine.state is State.IDLE

        ctx.play_action(role_id=1, action_id="nod")
        ctx.drain()
        assert len(motion.calls) == 2
        assert ctx.state_machine.state is State.IDLE
    finally:
        ctx.close()


def test_motion_busy_is_derived_from_the_active_token(tmp_path: Path) -> None:
    """``motion_busy`` 必须是派生量：播放中 True、收尾后 False，且不可单独写入。"""
    with _Rig(tmp_path, "derived.sqlite3") as rig:
        ctx = rig.context
        assert ctx.motion_busy is False
        ctx.play_action(role_id=1, action_id="wave")
        _wait_in_flight(rig)
        assert ctx.motion_busy is True
        assert rig.await_state("IDLE") == "IDLE"
        assert ctx.motion_busy is False
        with pytest.raises(AttributeError):
            ctx.motion_busy = True  # type: ignore[misc]  派生量不可写
