"""角色配置 schema（``role.json`` / ``manifest.json``，Pydantic）。

- 主机侧限位**真源** = 角色配置的 ``min_us`` / ``max_us``（架构 §10.4 T1）；固件 1400–1600μs
  仅作台架保守值保留，两边各自成立。因此这里校验 ``min_us``/``max_us`` 落在固件保守区间内。
- ``actions`` 是该角色的动作**白名单**；``play_action`` 只允许白名单内动作（§13）。
- 轴映射必须显式声明 4 轴，禁止硬拧（§10.2 D5 取舍的落地要求）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

FW_MIN_US = 1400
FW_MAX_US = 1600
AXIS_COUNT = 4
LANGUAGES = ("Auto", "zh", "en")


class RoleConfig(BaseModel):
    """一个可换公仔的配置。"""

    schema_version: int = Field(default=1, ge=1, le=1)
    role_id: int = Field(ge=1, le=3)
    name: str = Field(min_length=1, max_length=40)
    config_version: str = Field(min_length=1, max_length=40)
    min_us: int = Field(ge=FW_MIN_US, le=FW_MAX_US)
    max_us: int = Field(ge=FW_MIN_US, le=FW_MAX_US)
    neutral_us: int = Field(ge=FW_MIN_US, le=FW_MAX_US)
    axes: list[str] = Field(min_length=AXIS_COUNT, max_length=AXIS_COUNT)
    tts_voice: str = "Cherry"
    language: Literal["Auto", "zh", "en"] = "Auto"
    actions: list[str] = Field(default_factory=list)

    @field_validator("max_us")
    @classmethod
    def _max_gt_min(cls, value: int, info) -> int:
        min_us = info.data.get("min_us")
        if min_us is not None and value <= min_us:
            raise ValueError("max_us 必须大于 min_us")
        return value

    @field_validator("neutral_us")
    @classmethod
    def _neutral_in_range(cls, value: int, info) -> int:
        min_us = info.data.get("min_us")
        max_us = info.data.get("max_us")
        if min_us is not None and value < min_us:
            raise ValueError("neutral_us 不能小于 min_us")
        if max_us is not None and value > max_us:
            raise ValueError("neutral_us 不能大于 max_us")
        return value

    @field_validator("axes")
    @classmethod
    def _axes_nonempty(cls, value: list[str]) -> list[str]:
        if any(not axis.strip() for axis in value):
            raise ValueError("轴名不能为空")
        return value


class RoleManifest(BaseModel):
    """角色包导入前的清单（``manifest.json``）。"""

    name: str = Field(min_length=1, max_length=40)
    role_id: int = Field(ge=1, le=3)
    config_version: str = Field(min_length=1, max_length=40)
    files: list[str] = Field(default_factory=list)
