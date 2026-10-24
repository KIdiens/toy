"""目标位置来自实际交给传输层的 FRAME，不从动作文件或模拟状态猜测。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from host.web.app import create_app
from host.web.schemas import MotionTarget

from tests.integration.test_motion_interrupt import rig as rig


def wait_for(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    return None


def test_live_http_target_matches_transmitted_frame_and_clears_on_stop(rig):
    context, link = rig
    context.play_action(role_id=1, action_id="wave")
    assert link.frame_events[0].wait(2)
    with TestClient(create_app(context)) as client:
        target = wait_for(lambda: client.get("/api/v1/status").json()["data"].get("target"))
        assert target, "串口已经发出 FRAME，但状态接口没有实时目标"
        sent = next(line for line in link.sessions[0] if line.startswith(f"FRAME {target['seq']} "))
        assert target["target_us"] == [int(value) for value in sent.split()[2:]]
        assert target["source"] == "sent_frame" and target["role_id"] == 1
        assert target["valid_for_ms"] > 0
        MotionTarget.model_validate(target)
        with client.websocket_connect("/api/v1/events") as events:
            frame = events.receive_json()
            assert frame["kind"] == "STATUS"
            assert frame["target"]["source"] == "sent_frame"
            assert frame["axis_limits"]["role_id"] == 1
            wire = next(line for line in link.sessions[0]
                        if line.startswith(f"FRAME {frame['target']['seq']} "))
            assert frame["target"]["target_us"] == [int(v) for v in wire.split()[2:]]
        assert (
            client.post("/api/v1/actions/stop", json={"request_id": "stop-target"}).status_code
            == 200
        )
        assert client.get("/api/v1/status").json()["data"]["target"] is None
    assert link.closed.wait(1)


@pytest.mark.parametrize("frames_before_stop", [3, 75])
def test_stop_is_received_after_multiple_progress_updates(rig, frames_before_stop):
    context, link = rig
    context.play_action(role_id=1, action_id="wave")
    assert link.frame_events[0].wait(2)
    assert wait_for(
        lambda: sum(line.startswith("FRAME ") for line in link.sessions[0]) >= frames_before_stop,
        timeout=3,
    )
    assert context.status_snapshot()["target"] is not None
    context.stop_action()
    assert link.closed.wait(0.6)
    received = bytes(link.controllers[0].received_bytes).splitlines()
    assert received[-1] == b"STOP", "关闭前的 STOP 没有到达模拟 MCU"
    assert not link.controllers[0].armed
    assert context.status_snapshot()["target"] is None
