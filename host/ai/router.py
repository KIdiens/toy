"""``AiRouter``：ASR → 输入审核 → LLM → 工具 → **输出审核** → TTS 的编排。

铁律落地：

- **输出审核在合成/播放之前**（AC-19）：``review_output`` 判定不合格即替换固定话术，
  再交给 TTS；绝不「先说出来再撤回」。
- **工具调用经本地白名单二次校验**（AC-18）：参数非法 → 本地拒绝 + 固定话术，**不得编造**。
- **记忆/摘要数据化封装**（D8 / §0 第 5 条）：注入前必过 ``safety.wrap_untrusted_data``。
- 本模块**不 import 任何 HTTP 库**；网络只在注入的 provider 内部发生，核心不阻塞于此。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

from host.ai import safety
from host.ai.providers import (
    AsrProvider,
    AudioClip,
    LlmProvider,
    LlmReply,
    Message,
    TtsProvider,
)
from host.ai.tool_scope import check_request, collect_effects, has_pending_media
from host.ai.tools import ToolRegistry
from host.core.errors import ToolValidationError

PERSONA_DEFAULT = "你是桌面原创角色小星，热情、简洁、适合儿童交流，每次不超过 100 字。"
DEFAULT_VOICE = "Cherry"
SAFETY_CLAUSE = (
    "不知道就直说不知道，不编造。"
    "不索要住址、学校、密码等隐私。"
    "危险现实操作要引导去找成年人。"
    "播放工具返回 pending_reply 时，仅表示等待当前回复结束，尚未开始或完成。"
    "应说接下来会执行，不得声称已经播放或完成。"
)
MAX_TOOL_ROUNDS = 2
PENDING_COMPLETION = re.compile(
    r'(?:^|[。！？.!?])\s*(?:'
    r'我(?:已经|已|刚刚|刚才)(?:帮你|替你|为你)?(?:开始)?(?:播放|播完|挥手|点头|完成|执行)'
    r'|我(?:帮你|替你|为你)(?:已经|已)?(?:播完|播放好了|完成了|挥完手|点完头)'
    r'|(?:动作|表演|故事|音效)(?:已经|已)(?:播放|执行|完成|结束)'
    r"|I(?:'ve| have)?\s+(?:already|just)\s+(?:played|finished|completed|performed|started))",
    re.IGNORECASE,
)

MemoryProvider = Callable[[], Sequence[str]]
PersonaProvider = Callable[[], str]
VoiceProvider = Callable[[], str]


@dataclass(frozen=True, slots=True)
class AiResult:
    """一轮结果；performances 是待执行快照，refusal_ready 标记安全拒绝语音。"""

    user_text: str
    reply_text: str
    clip: AudioClip | None = None
    blocked: bool = False
    reason: str = ""
    tool_calls: tuple[str, ...] = ()
    tool_rejected: tuple[str, ...] = ()
    performances: tuple[dict, ...] = ()
    refusal_ready: bool = False


class AiRouter:
    """把三条 provider 与工具白名单编排成「一轮可播放的回复」。"""

    def __init__(
        self,
        asr: AsrProvider,
        llm: LlmProvider,
        tts: TtsProvider,
        registry: ToolRegistry,
        *,
        memory_provider: MemoryProvider | None = None,
        persona: str | None = None,
        voice: str | None = None,
        persona_provider: PersonaProvider | None = None,
        voice_provider: VoiceProvider | None = None,
        logger=None,
    ) -> None:
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.registry = registry
        self.memory_provider = memory_provider
        # 静态值保留为默认与向后兼容入口；给了 provider 时每轮现取（换角色要立刻生效）。
        self.persona = PERSONA_DEFAULT if persona is None else persona
        self.voice = DEFAULT_VOICE if voice is None else voice
        self.persona_provider = persona_provider
        self.voice_provider = voice_provider
        self.logger = logger

    # ---------- 人设 / 声部（每轮现取，支持随角色切换） ----------
    def _current_persona(self) -> str:
        if self.persona_provider is not None:
            return self.persona_provider()
        return self.persona

    def _current_voice(self) -> str:
        if self.voice_provider is not None:
            return self.voice_provider()
        return self.voice

    # ---------- 对外入口 ----------
    def respond_audio(self, wav: bytes, *, lang: str | None = None) -> AiResult:
        check_request()
        text = self.asr.transcribe(wav, lang)
        check_request()
        return self.respond_text(text, lang=lang)

    def respond_text(self, text: str, *, lang: str | None = None) -> AiResult:
        with collect_effects() as effects:
            result = self._respond_text(text, lang=lang)
        approved = () if result.blocked or result.tool_rejected else tuple(effects)
        return replace(result, performances=approved)

    def _respond_text(self, text: str, *, lang: str | None = None) -> AiResult:
        check_request()
        user_text = safety.neutralize(text)
        reviewed = safety.review_input(user_text)
        if not reviewed.ok:
            return self._finalize(user_text, reviewed.text, blocked=True, reason=reviewed.reason,
                                  lang=lang)

        messages = self._build_messages(user_text)
        check_request()
        first = self.llm.complete(messages, tools=self.registry.specs())
        check_request()
        final, used, rejected = self._run_tools(first, messages)
        if rejected:
            return self._finalize(user_text, safety.CANNOT_FABRICATE_TEMPLATE, blocked=True,
                                  reason="工具参数非法，本地拒绝", tool_rejected=tuple(rejected),
                                  lang=lang)
        # 必须用**工具回合之后**的最终答复：模型发工具调用时第一轮 text 通常为空，
        # 若沿用第一轮文本，工具结果永远到不了用户嘴里（会被输出审核当成「空输出」拦掉）。
        return self._finalize(user_text, final.text, tool_calls=tuple(used), lang=lang)

    # ---------- 内部 ----------
    def _build_messages(self, user_text: str) -> list[Message]:
        messages = [Message("system", f"{self._current_persona()}{SAFETY_CLAUSE}")]
        block = ""
        if self.memory_provider is not None:
            block = safety.wrap_untrusted_data(list(self.memory_provider()))
        if block:
            # 显式数据块 + 非指令声明（D8）；模型不得据此执行任何系统命令。
            messages.append(Message("system", block))
        messages.append(Message("user", user_text))
        return messages

    def _run_tools(self, reply: LlmReply, messages: list[Message]
                   ) -> tuple[LlmReply, list[str], list[str]]:
        """执行工具回合，返回 ``(最终回复, 已受理工具, 被拒工具)``。

        返回**最终回复**是这一段的关键：早前版本只返回 ``used/rejected``，把工具回合之后
        重新生成的答复丢在函数里（`reply = current` 是死赋值），于是工具调用的结果永远
        到不了用户——天气/提醒/内容三个工具在真实模型档位整体失效。
        """
        used: list[str] = []
        rejected: list[str] = []
        current = reply
        seen = {}
        for _round in range(MAX_TOOL_ROUNDS):
            if not current.tool_calls:
                break
            for call in current.tool_calls:
                check_request()
                try:
                    call_id = getattr(call, 'call_id', '')
                    previous = seen.get(call_id) if call_id else None
                    if previous is not None:
                        if previous[:2] != (call.name, call.arguments):
                            raise ToolValidationError('同一调用编号携带了不同工具或参数')
                        result = previous[2]
                    else:
                        result = self.registry.invoke(call.name, call.arguments)
                        if call_id:
                            seen[call_id] = (call.name, deepcopy(call.arguments), result)
                except ToolValidationError as exc:
                    rejected.append(call.name)
                    if self.logger is not None:
                        self.logger.warning("tool rejected: %s (%s)", call.name, exc)
                    continue
                if previous is None:
                    used.append(call.name)
                messages.append(Message("assistant", current.text or f"[调用 {call.name}]"))
                messages.append(Message("tool", f"{call.name} 返回: {result}"))
            if rejected:
                break
            check_request()
            current = self.llm.complete(messages, tools=self.registry.specs())
            check_request()
        if current.tool_calls and not rejected:
            rejected.extend(call.name for call in current.tool_calls)
            if self.logger is not None:
                self.logger.warning('工具回合上限后仍有未完成请求，本轮不执行播放任务')
        return current, used, rejected

    def _finalize(
        self,
        user_text: str,
        text: str,
        *,
        blocked: bool = False,
        reason: str = "",
        tool_calls: tuple[str, ...] = (),
        tool_rejected: tuple[str, ...] = (),
        lang: str | None = None,
    ) -> AiResult:
        reviewed = safety.review_output(text)
        final_text = reviewed.text if not reviewed.ok else text
        if reviewed.ok and has_pending_media() and PENDING_COMPLETION.search(final_text):
            final_text = ('Okay, I will play these tasks after this reply.' if lang == 'en'
                          else '好的，我会在回复结束后按顺序播放。')
            reason = reason or '播放任务尚待执行，已修正提前声称完成的表述'
        check_request()
        clip = self.tts.synthesize(final_text, self._current_voice(), lang)
        check_request()
        refusal = (safety.GUIDE_ADULT_TEMPLATE, safety.REFUSE_PRIVATE_TEMPLATE,
                   safety.CANNOT_FABRICATE_TEMPLATE)
        return AiResult(
            user_text=user_text,
            reply_text=final_text,
            clip=clip,
            blocked=blocked or not reviewed.ok,
            reason=reason or reviewed.reason,
            tool_calls=tool_calls,
            tool_rejected=tool_rejected,
            refusal_ready=((blocked or not reviewed.ok) and final_text in refusal
                           and clip is not None),
        )
