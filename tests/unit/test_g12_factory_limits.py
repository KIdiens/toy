"""任务 A：出厂机械限位**只许收窄**（``FACTORY_LIMITS`` + ``model_validator``）。

``role.json`` 的 ``min_us``/``max_us`` 是主机侧限位真源，但修改前它们只被校验落在固件
保守区间 1400–1600 内 —— 于是内容可以把 role2 从出厂的 1420–1580 写成 1400–1600，
1405μs 的动作就能一路下发到机械限位只有 1420 的舵机。这三个角色的出厂值属于**硬件事实**，
写死在代码里，内容只能收窄。

红灯基线（改动前实测）：role2 -> 1400-1600、role2 -> 1400-1580、role2 -> 1420-1600、
role3 -> 1400-1600 全部 ACCEPTED。
"""
from __future__ import annotations

import json

import pytest
from host.roles.registry import RoleRegistry
from host.roles.schema import RoleConfig
from pydantic import ValidationError

from tests.support import TOY_ROOT

BASE_ROLE2 = {
    "schema_version": 1,
    "role_id": 2,
    "name": "小波",
    "config_version": "1.0.0",
    "min_us": 1420,
    "max_us": 1580,
    "neutral_us": 1500,
    "axes": ["左臂", "右臂", "头部左右", "腰部左右"],
    "tts_voice": "Ethan",
    "language": "Auto",
    "actions": ["wave", "greet"],
}


def _role(role_id: int, min_us: int, max_us: int) -> dict:
    """按 role2 的骨架造一个任意角色配置；``neutral_us`` 取区间中点。"""
    data = dict(BASE_ROLE2)
    data.update(
        role_id=role_id,
        name=f"角色{role_id}",
        min_us=min_us,
        max_us=max_us,
        neutral_us=(min_us + max_us) // 2,
    )
    return data


def _factory_limits() -> dict[int, tuple[int, int]]:
    from host.roles.schema import FACTORY_LIMITS

    return FACTORY_LIMITS


def test_factory_baseline_table_is_the_hardware_truth():
    """出厂值必须与三个角色包的机械限位一致（硬件事实，不是内容决定的）。"""
    assert _factory_limits() == {1: (1400, 1600), 2: (1420, 1580), 3: (1430, 1570)}


@pytest.mark.parametrize(
    ("role_id", "min_us", "max_us"),
    [(1, 1400, 1600), (2, 1420, 1580), (3, 1430, 1570)],
)
def test_factory_values_still_load(role_id: int, min_us: int, max_us: int):
    """出厂值本身必须仍然可加载（否则现有角色包会整包挂掉）。"""
    cfg = RoleConfig.model_validate(_role(role_id, min_us, max_us))
    assert (cfg.min_us, cfg.max_us) == (min_us, max_us)


@pytest.mark.parametrize(
    ("role_id", "min_us", "max_us"),
    [(2, 1425, 1575), (3, 1440, 1560), (1, 1450, 1550)],
)
def test_narrowing_is_allowed(role_id: int, min_us: int, max_us: int):
    """收窄（抬下限 / 压上限）是合法的出厂裁剪。"""
    cfg = RoleConfig.model_validate(_role(role_id, min_us, max_us))
    assert (cfg.min_us, cfg.max_us) == (min_us, max_us)


@pytest.mark.parametrize(
    ("role_id", "min_us", "max_us"),
    [(2, 1400, 1600), (3, 1420, 1590)],
)
def test_widening_on_both_sides_is_rejected(role_id: int, min_us: int, max_us: int):
    """双边放宽到固件极限必须被拒。

    **判别力锚点**：把 ``FACTORY_LIMITS[2]`` 改成 ``(1400, 1600)`` 时，
    role2 -> 1400-1600 这条会变成合法 —— 本用例随即转红。
    """
    with pytest.raises(ValidationError):
        RoleConfig.model_validate(_role(role_id, min_us, max_us))


@pytest.mark.parametrize(
    ("min_us", "max_us", "why"),
    [(1400, 1580, "只放宽下限"), (1420, 1600, "只放宽上限")],
)
def test_single_side_widening_is_rejected(min_us: int, max_us: int, why: str):
    """单边放宽也必须拒：只抬上限一样能让动作打到机械限位外。"""
    with pytest.raises(ValidationError) as excinfo:
        RoleConfig.model_validate(_role(2, min_us, max_us))
    assert "只允许收窄" in str(excinfo.value), why


def test_error_message_names_role_factory_range_and_submitted_values():
    """报错必须能定位：角色、出厂区间、提交值、以及「只允许收窄」。"""
    with pytest.raises(ValidationError) as excinfo:
        RoleConfig.model_validate(_role(3, 1400, 1590))
    message = str(excinfo.value)
    assert "3" in message, "必须点出是哪个角色"
    assert "1430" in message and "1570" in message, "必须给出该角色的出厂区间"
    assert "1400" in message and "1590" in message, "必须回显提交值"
    assert "只允许收窄" in message


def test_unknown_role_id_fails_without_keyerror():
    """未知 role_id 走 ``role_id: le=3`` 的既有校验，不得因查表抛 ``KeyError``。"""
    with pytest.raises(ValidationError) as excinfo:
        RoleConfig.model_validate(_role(4, 1400, 1600))
    message = str(excinfo.value)
    assert "KeyError" not in message
    assert "role_id" in message


def test_shipped_role_packs_still_load_and_are_unmodified():
    """三个现有角色包零改动加载；并守卫 ``role.json`` 的限位仍是出厂值（内容未被改）。"""
    limits = _factory_limits()
    paths = sorted((TOY_ROOT / "content" / "roles").glob("*/role.json"))
    assert len(paths) == 3
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        role_id = data["role_id"]
        assert (data["min_us"], data["max_us"]) == limits[role_id], (
            f"{path} 的限位不是出厂值，说明内容文件被改动过"
        )

    registry = RoleRegistry(TOY_ROOT / "content" / "roles")
    loaded = registry.load_all()
    assert [c.role_id for c in loaded] == [1, 2, 3]
