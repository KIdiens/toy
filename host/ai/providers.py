"""AI 提供方 Protocol 与统一数据类（契约由 ADR-010 锁定）。

- ``AsrProvider.transcribe`` / ``LlmProvider.complete`` / ``TtsProvider.synthesize``
- Stub 与 Qwen 两套实现共用本文件的形状，**一处切换**（``AI_PROVIDER``）。
- 工具调用协议承载在 ``LlmReply.tool_calls``（§10.2 D7），Stub 亦支持，端到端可测。

本模块只含数据契约，不含任何网络或业务逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Message:
    """一条对话消息。``role`` ∈ {system, user, assistant, tool}。"""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """暴露给模型的工具描述（白名单内的子集）。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求调用某工具。``arguments`` 尚未校验，必须经本地白名单二次校验。"""

    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class LlmReply:
    """模型回复：文本与（可选）工具调用。"""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioClip:
    """合成得到的音频片段（WAV，PCM）。"""

    data: bytes
    sample_rate: int
    channels: int = 1
    media_type: str = "audio/wav"

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0 or self.channels <= 0:
            return 0.0
        # 16-bit PCM：16 bit/样本 => 2 bytes/样本
        frames = max(0, len(self.data) - 44) // (2 * self.channels)
        return frames / self.sample_rate

    @property
    def is_wav(self) -> bool:
        return self.data[:4] == b"RIFF" and self.data[8:12] == b"WAVE"


@runtime_checkable
class AsrProvider(Protocol):
    """语音转文字。"""

    def transcribe(self, wav: bytes, lang: str | None = None) -> str: ...


@runtime_checkable
class LlmProvider(Protocol):
    """文本生成（含工具调用）。"""

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 220,
    ) -> LlmReply: ...


@runtime_checkable
class TtsProvider(Protocol):
    """文字转语音。"""

    def synthesize(
        self, text: str, voice: str = "Cherry", lang: str | None = None
    ) -> AudioClip: ...
