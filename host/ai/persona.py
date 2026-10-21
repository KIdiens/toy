"""人设编译器：把结构化 ``PersonaConfig`` 编译成 system 提示的**第一段**。

为什么单独一层而不是在 schema 里拼字符串：人设是内容（可换角色包），编译是产品行为
（拼装顺序、默认话术、字数兜底）。把编译固定在这里，内容就只能换「填什么」，
不能换「怎么拼」——后者才是注入面的入口。

编译结果**不含**安全条款：安全条款由 ``AiRouter`` 在末尾统一追加，保证它永远是最后一段。
"""
from __future__ import annotations

from host.roles.schema import PersonaConfig

DEFAULT_PERSONA_MAX_CHARS = 100


def compile_persona(name: str, persona: PersonaConfig | None) -> str:
    """把结构化人设编译成 system 提示的第一段（**不含**安全条款）。

    ``persona is None``（旧角色包 / 未填）→ 兼容默认串，与历史 ``PERSONA_DEFAULT`` 同为
    「友好、简洁、适合儿童交流」的口径，只是角色名换成当前的 ``name``。
    有 persona → 按「身份、语气、说话方式、口头禅、忌讳、字数上限」固定顺序拼接；
    空字段整段跳过，不产生「语气：。」这种残句。
    """
    if persona is None:
        return (
            f"你是{name}，友好、简洁、适合儿童交流，"
            f"每次不超过 {DEFAULT_PERSONA_MAX_CHARS} 字。"
        )

    identity = f"你是{name}" + (f"，{persona.archetype}" if persona.archetype else "")
    parts = [f"{identity}。"]
    if persona.tone:
        parts.append(f"语气：{persona.tone}。")
    if persona.speech_style:
        parts.append(f"说话方式：{persona.speech_style}。")
    if persona.catchphrases:
        parts.append(f"口头禅：{'；'.join(persona.catchphrases)}。")
    if persona.avoid:
        parts.append(f"忌讳：{'；'.join(persona.avoid)}。")
    parts.append(f"每次不超过 {persona.max_chars} 字。")
    return "".join(parts)
