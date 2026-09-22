"""录音器：时长上限、削顶检测、VAD，并强制半双工（先停播再录）。

- 单次录制**上限 15s**（SPEC §10 边界）；超限直接截断到上限，不产生超长录音。
- 削顶检测：任一 16-bit 采样接近满量程即标记 ``clipped``。
- 极简 VAD：整体 RMS 低于静音阈值即 ``has_speech=False``（避免把环境噪声当语音）。
- 半双工：录制前若注入了 ``player``，先 ``stop()``（AC-21「正在播放时先停播再录」）。

诚实边界：本 VAD 是**流程级**判据，不替代真实声学测试（§10.1 C6）。
"""
from __future__ import annotations

import array
from dataclasses import dataclass

from host.audio.backend import Recording

MAX_RECORD_SECONDS = 15.0
MIN_RECORD_SECONDS = 0.3
SILENCE_RMS_FLOOR = 120  # 16-bit 满量程 32768，低于约 -48dBFS 视为无语音


@dataclass(frozen=True, slots=True)
class RecorderPolicy:
    max_seconds: float = MAX_RECORD_SECONDS
    min_seconds: float = MIN_RECORD_SECONDS
    silence_rms_floor: int = SILENCE_RMS_FLOOR


def _samples(pcm: bytes) -> array.array:
    buf = array.array("h")
    usable = len(pcm) - (len(pcm) % 2)
    buf.frombytes(pcm[:usable])
    return buf


def _rms(pcm: bytes) -> float:
    buf = _samples(pcm)
    if not buf:
        return 0.0
    total = sum(int(s) * int(s) for s in buf)
    return (total / len(buf)) ** 0.5


def _clipped(pcm: bytes, *, threshold: int = 32700) -> bool:
    return any(abs(s) >= threshold for s in _samples(pcm))


class Recorder:
    """在共享后端上串行化录制；与 ``Player`` 构成半双工对。"""

    def __init__(self, backend, *, policy: RecorderPolicy | None = None, player=None) -> None:
        self.backend = backend
        self.policy = policy or RecorderPolicy()
        self.player = player
        self.last: Recording | None = None

    def record(self, max_seconds: float | None = None) -> Recording:
        if self.player is not None:
            self.player.stop()  # 半双工：先停播再录（AC-21）
        limit = min(self.policy.max_seconds, max_seconds or self.policy.max_seconds)
        self.backend.open()
        recorded = self.backend.record(limit)
        result = self._postprocess(recorded, limit)
        self.last = result
        return result

    def _postprocess(self, recorded: Recording, limit: float) -> Recording:
        pcm = recorded.data
        duration = recorded.duration_s or _frames(pcm, recorded.channels) / recorded.sample_rate
        if duration > limit + 1e-6:
            keep = int(limit * recorded.sample_rate) * 2 * recorded.channels
            pcm = pcm[:keep]
            duration = limit
        has_speech = _rms(pcm) >= self.policy.silence_rms_floor
        return Recording(
            data=pcm,
            sample_rate=recorded.sample_rate,
            channels=recorded.channels,
            duration_s=duration,
            clipped=_clipped(pcm) or recorded.clipped,
            has_speech=has_speech and duration >= 0.05,
        )


def _frames(pcm: bytes, channels: int) -> int:
    if channels <= 0:
        return 0
    return len(pcm) // (2 * channels)
