"""进度必须对应同一条已发送 FRAME，观察者失败不改变安全协议。"""

from __future__ import annotations

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.clock import VirtualClock
from host.core.events import CommandKind
from host.core.state_machine import Trigger
from host.motion.client import MotionClient, MotionProgress
from host.motion.interpolation import sample

from tests.support import TOY_ROOT
from tests.unit.test_motion_cancellation import ACTION, TraceLink


@pytest.mark.parametrize("fail_observer", [False, True])
def test_observer_matches_wire_and_does_not_change_protocol(fail_observer):
    traces = []
    for observe in (False, True):
        clock = VirtualClock()
        link = TraceLink(clock)
        reports = []

        def record(progress, link=link, reports=reports):
            assert link.writes[-1][1] == (
                f"FRAME {progress.seq} " + " ".join(map(str, progress.target_us))
            )
            assert list(progress.target_us) == sample(ACTION, progress.progress_ms)
            reports.append(progress)
            if fail_observer:
                raise RuntimeError("诊断接收者失败")

        result = MotionClient(link, clock).play(ACTION, on_progress=record if observe else None)
        traces.append((result, link.writes))
        if observe:
            assert len(reports) == (1 if fail_observer else result.frames_sent)
            assert reports[0].seq == 0
    assert traces[0] == traces[1]


@pytest.fixture()
def context(tmp_path):
    ctx = build_context(
        load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
        simulate=True,
        clock=VirtualClock(),
        root=TOY_ROOT,
    )
    ctx.drain()
    yield ctx
    ctx.close()


def start(context, *, replace=False):
    context.play_action(role_id=1, action_id="wave", replace_current=replace)
    command = context.work.get(timeout=0)
    assert command.kind is CommandKind.PLAY_ACTION
    return command


def record(context, command, seq=1):
    version = context.roles.get(1).config_version
    context._record_motion_progress(
        command, version, MotionProgress(1, seq, 20, (1500,) * 4, context.clock.now_ms()))


def test_target_expires_and_snapshots_are_independent(context):
    command = start(context)
    assert context.status_snapshot()["target"] is None
    record(context, command)
    snapshot = context.status_snapshot()
    snapshot["target"]["target_us"][0] = 1
    snapshot["action"]["seq"] = 999
    assert context.status_snapshot()["target"]["target_us"] == [1500] * 4
    assert context.status_snapshot()["action"]["seq"] == 1
    context.clock.sleep(0.999)
    assert context.status_snapshot()["target"]["valid_for_ms"] == 1
    context.clock.sleep(0.001)
    assert context.status_snapshot()["target"] is None


@pytest.mark.parametrize("transition", ["stop", "replace", "fault", "role", "complete", "close"])
def test_old_callback_cannot_restore_invalid_target(context, transition):
    old = start(context)
    record(context, old)
    assert context.status_snapshot()["target"] is not None
    if transition == "replace":
        new = start(context, replace=True)
        assert context.status_snapshot()["target"] is None
        record(context, new, seq=2)
    elif transition == "fault":
        with context._lock:
            context._deliver(context.engine.apply(Trigger.SAFETY_TRIP))
    elif transition == "complete":
        with context._lock:
            context._end_play(old.payload["play_seq"])
    elif transition == "close":
        context.close()
    else:
        context.stop_action()
        if transition == "role":
            context.switch_active_role(2)
    record(context, old, seq=99)
    snapshot = context.status_snapshot()
    if transition == "replace":
        assert snapshot["target"]["seq"] == 2
    else:
        assert snapshot["target"] is None
    if transition == "role":
        assert snapshot["axis_limits"]["min_us"] == 1420
        assert snapshot["axis_limits"]["max_us"] == 1580


def test_config_revision_invalidates_target(context):
    command = start(context)
    record(context, command)
    old_version = context.roles.get(1).config_version
    config = context.roles.get(1).model_copy(update={"config_version": "qa-new"})
    context.roles._roles[1] = config  # 仅替换内存配置，不改内容包。
    assert context.status_snapshot()["target"] is None
    context._record_motion_progress(
        command, old_version, MotionProgress(1, 9, 20, (1500,) * 4, context.clock.now_ms()))
    assert context.status_snapshot()["target"] is None


def test_delayed_observer_does_not_refresh_old_wire_sample(context):
    command = start(context)
    progress = MotionProgress(1, 1, 20, (1500,) * 4, context.clock.now_ms())
    context.clock.sleep(1.001)
    context._record_motion_progress(command, context.roles.get(1).config_version, progress)
    assert context.status_snapshot()["target"] is None
