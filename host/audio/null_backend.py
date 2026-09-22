"""空音频后端：无设备阶段默认档位，以固定时长「虚拟录放」跑通全链路。

- ``record`` 不接触任何设备，返回**确定时长**的 16-bit PCM（内容按夹具/参数生成），
  形状与真实后端一致（时长、采样率、完成语义），使 E2E 无硬件可跑。
- ``play`` 只累加虚拟播放时长，不产生声音，但不阻塞、可被 ``stop`` 打断。
- 采集与播放**互斥**：任一方进行时 ``busy`` 为真，另一方必须先 ``stop``（半双工）。
"""
from __future__ import annotations

import threading

from host.audio.backend import DEFAULT_CHANNELS, DEFAULT_SAMPLE_RATE, Recording


class NullAudioBackend:
    """无设备后端：确定性、零真实等待、线程安全。"""

    name = "null"

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        default_record_s: float = 2.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.default_record_s = default_record_s
        self._lock = threading.Lock()
        self._open = False
        self._playing = False
        self._recording = False
        self.played: list[float] = []

    # ---------- 生命周期 ----------
    def open(self) -> None:
        with self._lock:
            self._open = True

    def close(self) -> None:
        with self._lock:
            self._open = False
            self._playing = False
            self._recording = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._playing or self._recording

    # ---------- 录放 ----------
    def record(self, max_seconds: float, *, seconds: float | None = None) -> Recording:
        if max_seconds <= 0:
            raise ValueError("max_seconds 必须为正")
        with self._lock:
            if self._playing:
                raise RuntimeError("半双工互斥：正在播放时不能录音")
            self._recording = True
        try:
            duration = min(self.default_record_s, max_seconds) if seconds is None else seconds
            duration = max(0.0, min(duration, max_seconds))
            frames = int(duration * self.sample_rate)
            data = b"\x00\x00" * frames * self.channels
            return Recording(
                data=data,
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_s=frames / self.sample_rate,
                clipped=False,
                has_speech=duration > 0.05,
            )
        finally:
            with self._lock:
                self._recording = False

    def play(self, clip, *, volume: float = 1.0) -> None:
        duration = float(getattr(clip, "duration_s", 0.0))
        with self._lock:
            if self._recording:
                raise RuntimeError("半双工互斥：正在录音时不能播放")
            self._playing = True
            self.played.append(duration)
        # 虚拟播放：无真实等待（真实后端由 sd_backend 承担阻塞）。

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._recording = False
