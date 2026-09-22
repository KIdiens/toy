"""音频后端抽象（ADR-008）：``open/record/play/stop/close`` 五契约。

- 全项目**只允许一个** ``AudioBackend`` 实例；采集与播放互斥（半双工，AC-21）。
- ``Recording`` 是录制结果的统一形状；时长/削顶/有无语音由后端或 Recorder 计算。
- 真实后端（``sd_backend``）与空后端（``null_backend``）实现同一协议；
  上层 ``Player``/``Recorder`` 只依赖本协议，不关心具体设备。
"""
from __future__ import annotations

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
