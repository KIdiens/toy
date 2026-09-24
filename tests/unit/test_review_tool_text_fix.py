"""复查整改（第一批）回归护栏：工具调用链的最终答复。

缺陷形态：`_run_tools` 只返回 `(used, rejected)`，工具回合之后重新生成的答复被丢在
函数里（`reply = current` 是死赋值），`respond_text` 仍用**第一轮**文本。真实模型发工具
调用时第一轮 text 通常为空 → 工具结果永远到不了用户嘴里，被输出审核当「空输出」拦掉。
"""
from __future__ import annotations

from host.ai.providers import AudioClip, LlmReply, Message, ToolCall
from host.ai.router import AiRouter
from host.ai.tools import ParamSpec, ToolRegistry

ANSWER = "北京今天晴，24 度。"
PREAMBLE = "我查一下。"


class _ScriptedAsr:
    def transcribe(self, wav: bytes, lang: str | None = None) -> str:
        return "今天天气怎么样"


class _SilentTts:
    def synthesize(self, text: str, voice: str = "Cherry", lang: str | None = None) -> AudioClip:
        return AudioClip(data=b"\x00\x00" * 160, sample_rate=16000)


class _ToolThenAnswerLlm:
    """第一轮只发工具调用（真实模型行为），之后给最终答复。"""

    def __init__(self, calls: tuple[ToolCall, ...], answer: str, *, preamble: str = "") -> None:
        self._calls = calls
        self._answer = answer
        self._preamble = preamble
        self.rounds = 0

    def complete(self, messages: list[Message], tools=None, max_tokens: int = 220) -> LlmReply:
        self.rounds += 1
        if self.rounds == 1:
            return LlmReply(text=self._preamble, tool_calls=self._calls)
        return LlmReply(text=self._answer)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "weather_now", "查询当前天气",
        (ParamSpec("city", str, required=True),),
        lambda args: {"ok": True, "city": args["city"], "temp_c": 24.0},
    )
    return registry


def _router(llm) -> AiRouter:
    return AiRouter(_ScriptedAsr(), llm, _SilentTts(), _registry())


def test_tool_round_final_text_reaches_the_user() -> None:
    llm = _ToolThenAnswerLlm((ToolCall("weather_now", {"city": "北京"}, "c1"),), ANSWER)
    result = _router(llm).respond_text("今天天气怎么样")

    assert llm.rounds == 2, "工具回合后应当再问一次模型要最终答复"
    assert result.reply_text == ANSWER, "工具回合后的最终答复被丢弃了"
    assert result.blocked is False, f"最终答复被当成异常拦下：{result.reason!r}"
    assert result.tool_calls == ("weather_now",), "已执行工具必须被记录"


def test_first_round_preamble_does_not_replace_the_final_answer() -> None:
    """模型爱先来一句「我查一下。」——那**不是**最终答复，不得当作回复说给用户。"""
    llm = _ToolThenAnswerLlm((ToolCall("weather_now", {"city": "北京"}, "c1"),), ANSWER,
                             preamble=PREAMBLE)
    result = _router(llm).respond_text("今天天气怎么样")

    assert result.reply_text == ANSWER
    assert result.reply_text != PREAMBLE


def test_rejected_tool_arguments_still_use_the_fixed_template() -> None:
    """AC-18 不能被这次修复带跑：参数非法必须本地拒绝并给固定话术，不得编造。"""
    llm = _ToolThenAnswerLlm((ToolCall("weather_now", {}, "c1"),), ANSWER)
    result = _router(llm).respond_text("今天天气怎么样")

    assert result.blocked is True
    assert result.tool_rejected == ("weather_now",)
    assert result.reply_text != ANSWER, "工具参数非法时不得采用模型答复"
