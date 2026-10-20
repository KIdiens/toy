"""P2-2：``/actions/validate`` 对**未加载的角色**谎报 ``valid: True``。

## 缺陷

``role_limits`` 在角色未加载时回落到固件区间（1400–1600）——这对**播放路径**是正确
的兜底，但 ``validate_action`` 复用了它，于是「角色 2 根本没装」这件事在编辑态校验里
被吞掉：``roles=[1,3]`` 时 ``validate_action(role_id=2, 1405us) -> {'valid': True}``，
而 role2 的真实限幅是 1420–1580，这个动作真拿去播必定越限。

## 修法边界

- ``validate_action``：角色未加载 → ``valid: False`` + 「角色 N 未加载，无法按机械
  限幅校验」；
- ``role_limits`` 的回落**一个字不改** —— ``execution._run_play`` 的下发前复核依赖它，
  校验器变严不得阻断播放路径。

## 误伤面（如实记录）

想在**未安装的角色**上做预校验的内容作者会拿到 ``valid: False``。这是事实陈述而非
误判：没有该角色的 ``role.json`` 就没有机械限幅，任何「合法」结论都是编出来的。
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.roles.schema import FW_MAX_US, FW_MIN_US

from tests.support import TOY_ROOT

MISSING_ROLE = 2
LOADED_ROLES = (1, 3)


def _root_without(tmp_path: Path, missing: int = MISSING_ROLE) -> Path:
    """复制内容区后删掉指定角色 —— 模拟「只装了 1、3 两个公仔」。"""
    root = tmp_path / "root"
    shutil.copytree(TOY_ROOT / "content", root / "content")
    shutil.rmtree(root / "content" / "roles" / str(missing))
    return root


def _context(tmp_path: Path, name: str = "p22.sqlite3", *, root: Path | None = None):
    settings = load_settings({"DB_PATH": str(tmp_path / name)})
    return build_context(settings, simulate=True, root=root or _root_without(tmp_path))


def _span_for(peak_us: int) -> int:
    """满足 300μs/s 速度约束的最小帧间距（ms）—— 否则会被速度规则先拒。"""
    return int(math.ceil(1.5 * abs(peak_us - 1500) / 300 * 1000)) + 100


def _action(role_id: int, peak_us: int = 1500) -> dict:
    span = _span_for(peak_us)
    return {
        "schema": 1,
        "role_id": role_id,
        "name": "限幅校验样本",
        "frames": [
            {"t_ms": 0, "us": [1500, 1500, 1500, 1500]},
            {"t_ms": span, "us": [peak_us, 1500, 1500, 1500]},
            {"t_ms": span * 2, "us": [1500, 1500, 1500, 1500]},
        ],
    }


# =====================================================================
# 1. 缺陷本体
# =====================================================================
def test_unloaded_role_is_not_reported_valid(tmp_path: Path) -> None:
    """``roles=[1,3]`` 时 role2 的动作必须判非法，并说明「未加载」。"""
    context = _context(tmp_path)
    try:
        assert context.roles.exists(1) and context.roles.exists(3)
        assert not context.roles.exists(MISSING_ROLE), "装置失败：role2 还在"

        verdict = context.validate_action(_action(MISSING_ROLE, peak_us=1405))

        assert verdict["valid"] is False, (
            f"未安装的角色仍被判合法：{verdict} —— 编辑器会把这个动作当成可播放"
        )
        message = " ".join(item["message"] for item in verdict["errors"])
        assert str(MISSING_ROLE) in message and "未加载" in message, message
    finally:
        context.close()


def test_the_sample_value_is_only_legal_under_the_firmware_fallback(tmp_path: Path) -> None:
    """钉死缺陷前提：1405μs 落在**固件**区间内、却在 role2 的真实限幅之外。

    这条若转红，说明内容或限幅变了，缺陷样本失去针对性（不是修复本身坏了）。
    """
    context = _context(tmp_path)
    try:
        assert FW_MIN_US <= 1405 <= FW_MAX_US
        assert context.role_limits(MISSING_ROLE) == (FW_MIN_US, FW_MAX_US), (
            "role2 未加载时 role_limits 必须回落到固件区间（播放路径的兜底）"
        )
    finally:
        context.close()


def test_playback_fallback_is_unchanged_by_the_stricter_validator(tmp_path: Path) -> None:
    """``role_limits`` 的回落不得被这次收紧连带改掉（``_run_play`` 依赖它）。"""
    context = _context(tmp_path)
    try:
        assert context.role_limits(MISSING_ROLE) == (FW_MIN_US, FW_MAX_US)
        # 已加载角色仍返回它自己的限幅（回落的对照面）
        assert context.role_limits(3) == (1430, 1570)
    finally:
        context.close()


# =====================================================================
# 2. 不得误伤：已加载角色照旧、格式错误照旧报格式
# =====================================================================
def test_loaded_roles_still_validate_legal_actions(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        for role_id in LOADED_ROLES:
            verdict = context.validate_action(_action(role_id, peak_us=1500))
            assert verdict["valid"] is True, f"role{role_id}: {verdict}"
    finally:
        context.close()


def test_loaded_role_out_of_limits_error_still_names_the_limits(tmp_path: Path) -> None:
    """已加载角色越限幅：仍是原来的「脉宽超出角色限幅 …」文本，不被新分支取代。"""
    context = _context(tmp_path)
    try:
        verdict = context.validate_action(_action(3, peak_us=1400))
        assert verdict["valid"] is False
        message = " ".join(item["message"] for item in verdict["errors"])
        assert "角色限幅" in message and "1430" in message and "1570" in message, message
    finally:
        context.close()


def test_malformed_action_on_loaded_role_still_reports_format_error(tmp_path: Path) -> None:
    """格式错误仍走 ``validate`` 的原文，不被新分支抢先（诊断信息不能退化）。"""
    context = _context(tmp_path)
    try:
        for bad in ([1, 2], {"role_id": 1, "frames": []},
                    {"schema": 1, "role_id": "1", "frames": []}):
            verdict = context.validate_action(bad)  # type: ignore[arg-type]
            assert verdict["valid"] is False, bad
            message = " ".join(item["message"] for item in verdict["errors"])
            assert "未加载" not in message, f"{bad}: 格式错误被误报成角色未加载"
    finally:
        context.close()


def test_out_of_range_role_number_still_reports_format_error(tmp_path: Path) -> None:
    """role_id=9 是「编号错误」而不是「未安装」：不许把格式问题说成安装问题。"""
    context = _context(tmp_path)
    try:
        verdict = context.validate_action(_action(9, peak_us=1500))
        assert verdict["valid"] is False
        message = " ".join(item["message"] for item in verdict["errors"])
        assert "角色编号错误" in message, message
    finally:
        context.close()


# =====================================================================
# 3. 播放链路不受影响（角色 1 仍能播、表演键仍能解析 greet）
# =====================================================================
def test_play_action_for_loaded_role_still_schedules(tmp_path: Path) -> None:
    context = _context(tmp_path, "play.sqlite3")
    try:
        context.switch_active_role(1)
        if context.sim is not None:
            context.sim.role_res.role = 1
            context.clock.sleep(0.3)
        result = context.play_action(role_id=1, action_id="greet")
        assert result["scheduled"] is True
        assert context.fault is None
        context.drain()
        assert context.motion_busy is False
    finally:
        context.close()


def test_greet_resolution_still_works_for_loaded_role(tmp_path: Path) -> None:
    """表演键路径（``resolve_greet_action`` → ``validate_action``）不得被收紧打断。"""
    context = _context(tmp_path, "greet.sqlite3")
    try:
        context.switch_active_role(1)
        resolved = context.resolve_greet_action()
        assert resolved is not None, "已加载角色的 greet 解析失败 —— 播放路径被误伤"
        assert resolved["role_id"] == 1
    finally:
        context.close()
