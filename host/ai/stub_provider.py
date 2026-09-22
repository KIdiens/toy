"""确定性桩提供方：夹具驱动，**零网络出口**（AC-17）。

- 无 ``DASHSCOPE_API_KEY`` 时系统默认落桩；本模块**不 import 任何 HTTP 库**，
  从源头保证「启动与对话零网络请求」可被断言。
- ASR 返回脚本化文本；LLM 按关键词规则返回文本或 ``tool_calls``；TTS 生成固定 WAV。
- 供端到端与回归使用：同样输入 → 同样输出，无时间、无随机。
"""
from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from host.ai.providers import AudioClip, LlmReply, Message, ToolCall, ToolSpec

DEFAULT_REPLY = "我在呢，我们一起玩吧。"
DEFAULT_TRANSCRIPT = "你好呀"


def _silent_wav(frames: int, sample_rate: int, channels: int = 1) -> bytes:
    """生成确定性静音 WAV（16-bit PCM），供桩 TTS 充当音频。"""
    frames = max(0, int(frames))
    data = b"\x00\x00" * frames * channels
    byte_rate = sample_rate * channels * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate,
                                    channels * 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def _tool_call(raw: Mapping[str, Any]) -> ToolCall:
    return ToolCall(
        name=str(raw.get("name", "")),
        arguments=dict(raw.get("arguments", {})),
        call_id=str(raw.get("call_id", "")),
    )


def _llm_reply(raw: Mapping[str, Any]) -> LlmReply:
    calls = tuple(_tool_call(c) for c in raw.get("tool_calls", ()))
    return LlmReply(text=str(raw.get("text", "")), tool_calls=calls)


class StubAsr:
    """脚本化 ASR：固定转写文本，零网络。"""

    def __init__(self, transcript: str = DEFAULT_TRANSCRIPT) -> None:
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, wav: bytes, lang: str | None = None) -> str:
        self.calls += 1
        return self.transcript


class StubLlm:
    """关键词规则驱动的 LLM：命中即返回脚本化文本/工具调用，未命中返回默认文本。"""

    def __init__(
        self,
        *,
        rules: Mapping[str, Mapping[str, Any]] | None = None,
        default: str = DEFAULT_REPLY,
        summary: str = "好的，我知道了。",
    ) -> None:
        self._rules = {k: _llm_reply(v) for k, v in dict(rules or {}).items()}
        self._default = default
        self._summary = summary
        self.calls = 0

    @classmethod
    def from_fixture(cls, path: str | Path) -> StubLlm:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(rules=data.get("rules", {}), default=data.get("default", DEFAULT_REPLY))

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 220,
    ) -> LlmReply:
        self.calls += 1
        if messages and messages[-1].role == "tool":
            return LlmReply(text=self._summary)  # 工具结果已回填 → 收尾，避免重复调用
        user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
        for key, reply in self._rules.items():
            if key in user_text:
                return reply
        return LlmReply(text=self._default)


class StubTts:
    """确定性 TTS：按时长生成静音 WAV（同长文本 → 同长音频）。"""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        seconds_per_char: float = 0.05,
        min_seconds: float = 0.3,
    ) -> None:
        self.sample_rate = sample_rate
        self.seconds_per_char = seconds_per_char
        self.min_seconds = min_seconds
        self.calls = 0

    def synthesize(self, text: str, voice: str = "Cherry", lang: str | None = None) -> AudioClip:
        self.calls += 1
        seconds = max(self.min_seconds, len(text) * self.seconds_per_char)
        frames = int(seconds * self.sample_rate)
        return AudioClip(
            data=_silent_wav(frames, self.sample_rate),
            sample_rate=self.sample_rate,
        )


def build_stub_triad(
    *,
    transcript: str = DEFAULT_TRANSCRIPT,
    fixture: str | Path | None = None,
) -> tuple[StubAsr, StubLlm, StubTts]:
    """一次性构造 ASR/LLM/TTS 桩（可选从夹具加载 LLM 规则）。"""
    llm = StubLlm.from_fixture(fixture) if fixture is not None else StubLlm()
    return StubAsr(transcript), llm, StubTts()
