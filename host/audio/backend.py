"""音频后端抽象（ADR-008）：``open/record/play/stop/close`` 五契约。

- 全项目**只允许一个** ``AudioBackend`` 实例；采集与播放互斥（半双工，AC-21）。
- ``Recording`` 是录制结果的统一形状；时长/削顶/有无语音由后端或 Recorder 计算。
- 真实后端（``sd_backend``）与空后端（``null_backend``）实现同一协议；
  上层 ``Player``/``Recorder`` 只依赖本协议，不关心具体设备。
- 支持操作取消的后端声明 ``supports_cancel=True``，并接收可选 ``cancel`` 事件；
  不支持该扩展的已有测试后端仍可使用原来的五个方法。
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1


@dataclass(frozen=True, slots=True)
class Recording:
    """一次录制的统一结果。"""

    data: bytes
    sample_rate: int
    channels: int = DEFAULT_CHANNELS
    duration_s: float = 0.0
    clipped: bool = False
    has_speech: bool = True

    @property
    def is_empty(self) -> bool:
        return subframes(self.data, self.channels) == 0


def subframes(pcm: bytes, channels: int = DEFAULT_CHANNELS) -> int:
    """16-bit PCM 的采样帧数。"""
    if channels <= 0:
        return 0
    return max(0, len(pcm) - (44 if pcm[:4] == b"RIFF" else 0)) // (2 * channels)


def recording_wav(recording: Recording) -> bytes:
    """ASR 入口统一使用 WAV；录音后端与 VAD 仍保留 16-bit PCM 契约。"""
    if not recording.data or recording.data[:4] == b"RIFF":
        return recording.data  # 文件夹具已带 WAV 头时不重复封装。
    if recording.sample_rate <= 0 or recording.channels <= 0:
        raise ValueError("录音采样率与声道数必须为正，不能封装为 WAV")
    if len(recording.data) % (2 * recording.channels):
        raise ValueError("16-bit PCM 录音包含不完整采样帧")
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(recording.channels)
        handle.setsampwidth(2)
        handle.setframerate(recording.sample_rate)
        handle.writeframes(recording.data)
    return output.getvalue()


@runtime_checkable
class AudioBackend(Protocol):
    """音频设备最小契约。实现方必须线程安全地维护 ``busy``。"""

    name: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    def record(self, max_seconds: float) -> Recording: ...

    def play(self, clip, *, volume: float = 1.0) -> None: ...

    def stop(self) -> None: ...

    @property
    def busy(self) -> bool:
        """是否正在采集或播放（用于半双工互斥判定）。"""
        ...
