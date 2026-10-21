"""角色配置 schema（``role.json`` / ``manifest.json``，Pydantic）。

- 主机侧限位**真源** = 角色配置的 ``min_us`` / ``max_us``（架构 §10.4 T1）；固件 1400–1600μs
  仅作台架保守值保留，两边各自成立。字段约束仍要求落在固件保守区间内。
- **出厂机械限位是不可被内容放宽的基线**（``FACTORY_LIMITS``）：这三个值来自每个公仔的
  机械行程，是**硬件事实**，不由内容决定。内容可以把区间改窄（保护更保守），但不能改宽 ——
  否则 role2 把自己写成 1400–1600，1405μs 的动作就能连过三道防线下发到机械限位 1420 的舵机。
- ``actions`` 是该角色的动作**白名单**；``play_action`` 只允许白名单内动作（§13）。
- 轴映射必须显式声明 4 轴，禁止硬拧（§10.2 D5 取舍的落地要求）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FW_MIN_US = 1400
FW_MAX_US = 1600
AXIS_COUNT = 4
LANGUAGES = ("Auto", "zh", "en")

# 出厂机械限位（μs）：每个公仔的机械行程，**写死在代码里**。理由见模块 docstring ——
# 若允许内容定义它，坏/恶意角色包就能把「出厂事实」改写成固件保守值，等于取消这道防线。
FACTORY_LIMITS: dict[int, tuple[int, int]] = {
    1: (1400, 1600),
    2: (1420, 1580),
    3: (1430, 1570),
}

# 音色名只做**格式**校验，不做音色白名单（见 ``_voice_format``）。
VOICE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,19}")


def _clean_persona_text(value, *, field: str, limit: int) -> str:
    """人设字段的统一清洗：空白 → 未填；含破坏提示结构的字符 → 直接拒。

    为什么「拒绝」而不是「过一遍 ``safety.neutralize``」：``neutralize`` 保留换行
    （``ch == "\\n" or ch >= " "``），也不剔零宽字符 —— 它服务的是朗读/送模型路径，
    不是「能否安全嵌入 system 提示」的判定。人设字段的正确语义是**一条单行短语**：
    出现换行、控制字符或零宽字符本身就说明这不是正常填写，压缩掉反而会静默改变含义
    （``"热情\\n忽略上面的规则"`` 压成一行就成了合法语气）。
    """
    if not isinstance(value, str):  # 非字符串交给 pydantic 报类型错误
        return value
    if not value.strip():
        return ""
    if "<" in value or ">" in value:
        raise ValueError(f"{field} 不允许包含尖括号 '<' / '>'（会切断或伪造提示段落）")
    if any(ch in "\r\n\t" for ch in value):
        raise ValueError(f"{field} 不允许换行或制表符（system 提示按行/段落组织）")
    bad = [ch for ch in value if unicodedata.category(ch).startswith("C")]
    if bad:
        raise ValueError(
            f"{field} 不允许控制字符或零宽字符（遇到 U+{ord(bad[0]):04X}）"
        )
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValueError(f"{field} 最多 {limit} 字，当前 {len(cleaned)} 字")
    return cleaned


class PersonaConfig(BaseModel):
    """结构化人设（DIY 性格的地基）。

    刻意**不做**自由 prompt 字段：自由文本等于把 system 提示的拼装权交给内容，
    注入面无法收敛。这里只有可枚举的短字段，由 :func:`host.ai.persona.compile_persona`
    固定顺序拼装。所有字段可缺省 —— 旧角色包不带 ``persona`` 段照常加载。
    """

    archetype: str = ""
    tone: str = ""
    speech_style: str = ""
    catchphrases: list[str] = Field(default_factory=list, max_length=3)
    avoid: list[str] = Field(default_factory=list, max_length=3)
    max_chars: int = Field(default=100, ge=20, le=300)

    @field_validator("archetype", mode="before")
    @classmethod
    def _clean_archetype(cls, value):
        return _clean_persona_text(value, field="archetype", limit=40)

    @field_validator("tone", mode="before")
    @classmethod
    def _clean_tone(cls, value):
        return _clean_persona_text(value, field="tone", limit=60)

    @field_validator("speech_style", mode="before")
    @classmethod
    def _clean_speech_style(cls, value):
        return _clean_persona_text(value, field="speech_style", limit=80)

    @field_validator("catchphrases", "avoid", mode="before")
    @classmethod
    def _clean_phrases(cls, value, info):
        if not isinstance(value, (list, tuple)):
            return value
        cleaned = []
        for item in value:
            text = _clean_persona_text(item, field=info.field_name, limit=20)
            if text == "":
                continue  # 空白项视为未填，丢弃而不是报错
            cleaned.append(text)
        return cleaned


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
    persona: PersonaConfig | None = None

    @field_validator("tts_voice")
    @classmethod
    def _voice_format(cls, value: str) -> str:
        """音色名只校验**格式**，不校验集合。

        为什么不做白名单：``voice`` 会被原样透传给云端 TTS（如 DashScope），可用音色集合
        由上游决定、且会随版本增删；本仓库没有权威清单，硬编码一份必然漂移 ——
        漂移的方向就是**误杀合法音色**（用户填了对的名字却被告知不存在）。
        格式校验挡的是另一类风险：把任意字符串 / JSON 片段 / 带分隔符的载荷塞进
        云 API 参数。代价是理论上可能放过「格式合法但上游不存在」的音色，
        这类错误由 TTS 调用失败兜底（AI_FAIL → 固定话术），不会静默出声。
        """
        if not VOICE_NAME_RE.fullmatch(value):
            raise ValueError("音色名只允许字母数字、≤20 字符（首字符必须是字母）")
        return value

    @model_validator(mode="after")
    def _within_factory_limits(self) -> RoleConfig:
        """出厂机械限位只许收窄，不许放宽。

        用 ``model_validator`` 而非 ``field_validator``：判定要同时看 ``min_us`` /
        ``max_us`` / ``role_id`` 三者，字段级校验器在 ``max_us`` 上看不到完整上下文。
        """
        factory = FACTORY_LIMITS.get(self.role_id)
        if factory is None:
            # role_id 已被 Field(ge=1, le=3) 挡住；这里再兜一层是为了「将来加角色但忘了
            # 补 FACTORY_LIMITS」时得到明确的校验错误，而不是 KeyError 直接把加载打断。
            raise ValueError(
                f"角色 {self.role_id} 没有登记出厂机械限位（FACTORY_LIMITS），无法校验"
            )
        factory_min, factory_max = factory
        if self.min_us < factory_min or self.max_us > factory_max:
            raise ValueError(
                f"角色 {self.role_id}（{self.name}）的出厂机械限位是 "
                f"{factory_min}-{factory_max}μs，内容只允许收窄"
                f"（min_us 不低于 {factory_min}、max_us 不高于 {factory_max}）；"
                f"当前填写 {self.min_us}-{self.max_us}μs"
            )
        return self

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
