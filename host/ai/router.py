"""``AiRouter``：ASR → 输入审核 → LLM → 工具 → **输出审核** → TTS 的编排。

铁律落地：

- **输出审核在合成/播放之前**（AC-19）：``review_output`` 判定不合格即替换固定话术，
  再交给 TTS；绝不「先说出来再撤回」。
- **工具调用经本地白名单二次校验**（AC-18）：参数非法 → 本地拒绝 + 固定话术，**不得编造**。
- **记忆/摘要数据化封装**（D8 / §0 第 5 条）：注入前必过 ``safety.wrap_untrusted_data``。
- 本模块**不 import 任何 HTTP 库**；网络只在注入的 provider 内部发生，核心不阻塞于此。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from host.ai import safety
from host.ai.providers import (
    AsrProvider,
    AudioClip,
    LlmProvider,
    LlmReply,
    Message,
    TtsProvider,
)
from host.ai.tools import ToolRegistry
from host.core.errors import ToolValidationError

PERSONA_DEFAULT = "你是桌面原创角色小星，热情、简洁、适合儿童交流，每次不超过 100 字。"
SAFETY_CLAUSE = (
    "不知道就直说不知道，不编造。"
    "不索要住址、学校、密码等隐私。"
    "危险现实操作要引导去找成年人。"
)
MAX_TOOL_ROUNDS = 2

MemoryProvider = Callable[[], Sequence[str]]


@dataclass(frozen=True, slots=True)
class AiResult:
    """一轮对话的结果。``clip`` 可能在纯文本模式下为 ``None``。"""

    user_text: str
    reply_text: str
    clip: AudioClip | None = None
    blocked: bool = False
    reason: str = ""
    tool_calls: tuple[str, ...] = ()
    tool_rejected: tuple[str, ...] = ()


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
        persona: str = PERSONA_DEFAULT,
        voice: str = "Cherry",
        logger=None,
    ) -> None:
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.registry = registry
        self.memory_provider = memory_provider
        self.persona = persona
        self.voice = voice
        self.logger = logger

    # ---------- 对外入口 ----------
    def respond_audio(self, wav: bytes, *, lang: str | None = None) -> AiResult:
        text = self.asr.transcribe(wav, lang)
        return self.respond_text(text, lang=lang)

    def respond_text(self, text: str, *, lang: str | None = None) -> AiResult:
        user_text = safety.neutralize(text)
        reviewed = safety.review_input(user_text)
        if not reviewed.ok:
            return self._finalize(user_text, reviewed.text, blocked=True, reason=reviewed.reason,
                                  lang=lang)

        messages = self._build_messages(user_text)
        first = self.llm.complete(messages, tools=self.registry.specs())
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
        messages = [Message("system", f"{self.persona}{SAFETY_CLAUSE}")]
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
        """执行工具回合，返回 ``(最终回复, 已执行工具, 被拒工具)``。

        返回**最终回复**是这一段的关键：早前版本只返回 ``used/rejected``，把工具回合之后
        重新生成的答复丢在函数里（`reply = current` 是死赋值），于是工具调用的结果永远
        到不了用户——天气/提醒/内容三个工具在真实模型档位整体失效。
        """
        used: list[str] = []
        rejected: list[str] = []
        current = reply
        for _round in range(MAX_TOOL_ROUNDS):
            if not current.tool_calls:
                break
            for call in current.tool_calls:
                try:
                    result = self.registry.invoke(call.name, call.arguments)
                except ToolValidationError as exc:
                    rejected.append(call.name)
                    if self.logger is not None:
                        self.logger.warning("tool rejected: %s (%s)", call.name, exc)
                    continue
                used.append(call.name)
                messages.append(Message("assistant", current.text or f"[调用 {call.name}]"))
                messages.append(Message("tool", f"{call.name} 返回: {result}"))
            if rejected:
                break
            current = self.llm.complete(messages, tools=self.registry.specs())
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
        clip = self.tts.synthesize(final_text, self.voice, lang)
        return AiResult(
            user_text=user_text,
            reply_text=final_text,
            clip=clip,
            blocked=blocked or not reviewed.ok,
            reason=reason or reviewed.reason,
            tool_calls=tool_calls,
            tool_rejected=tool_rejected,
        )
