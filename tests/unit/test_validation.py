"""动作校验边界（AC-09 / AC-10）：数值、时间、峰值速度、角色与帧数。"""
from __future__ import annotations

import copy

import pytest
from host.motion.validation import validate


@pytest.mark.parametrize("bad", [1399, 1601, float("nan"), float("inf"), True, "1500"])
def test_reject_out_of_range_and_nonfinite(wave_action, bad):  # AC-09
    action = copy.deepcopy(wave_action)
    action["frames"][1]["us"][0] = bad
    with pytest.raises(ValueError):
        validate(action)


def test_accept_boundary_values(wave_action):
    action = copy.deepcopy(wave_action)
    action["frames"][1]["us"][0] = 1400
    action["frames"][2]["us"][0] = 1600
    validate(action)


def test_reject_duplicate_timestamp(wave_action):  # AC-10
    action = copy.deepcopy(wave_action)
    action["frames"][1]["t_ms"] = 0
    with pytest.raises(ValueError, match="严格递增"):
        validate(action)


def test_reject_excessive_speed(wave_action):  # AC-10
    action = copy.deepcopy(wave_action)
    action["frames"][1]["t_ms"] = 100
    with pytest.raises(ValueError, match="峰值速度"):
        validate(action)


def test_reject_bad_schema_and_role(wave_action):
    for field, value in (("schema", 2), ("role_id", 9), ("role_id", True)):
        action = copy.deepcopy(wave_action)
        action[field] = value
        with pytest.raises(ValueError):
            validate(action)


def test_reject_frame_count(wave_action):
    action = copy.deepcopy(wave_action)
    action["frames"] = action["frames"][:1]
    with pytest.raises(ValueError, match="关键帧数量"):
        validate(action)
    action = copy.deepcopy(wave_action)
    action["frames"] = action["frames"] * 300
    with pytest.raises(ValueError):
        validate(action)


def test_reject_time_bounds(wave_action):
    action = copy.deepcopy(wave_action)
    action["frames"][1]["t_ms"] = 60001
    with pytest.raises(ValueError, match="0 至 60000"):
        validate(action)
    action = copy.deepcopy(wave_action)
    action["frames"][1]["t_ms"] = 1.5
    with pytest.raises(ValueError):
        validate(action)


def test_reject_axis_count(wave_action):
    action = copy.deepcopy(wave_action)
    action["frames"][1]["us"] = [1500, 1500, 1500]
    with pytest.raises(ValueError, match="四个轴"):
        validate(action)


def test_first_and_last_frame_constraints(wave_action):
    action = copy.deepcopy(wave_action)
    action["frames"][0]["us"] = [1500, 1600, 1500, 1500]
    with pytest.raises(ValueError, match="中位"):
        validate(action)
    action = copy.deepcopy(wave_action)
    action["frames"][-1]["us"] = [1500, 1540, 1500, 1500]
    with pytest.raises(ValueError, match="末帧回到中位"):
        validate(action)


def test_returns_same_action_object(wave_action):
    assert validate(wave_action) is wave_action
