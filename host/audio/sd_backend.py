"""真实声卡后端：``sounddevice`` + ``soundfile``（ADR-008）。

- **延迟导入**：本模块在缺 ``sounddevice``/``soundfile`` 的机器上仍可被 import；
  只有真正调用 ``open``/``record``/``play`` 时才要求依赖存在，缺失则抛 ``ConfigError``
  （不吞异常、给出可执行的安装指引）。
- 同一 PortAudio API 覆盖 Windows WASAPI 与树莓派 ALSA；全局单实例、半双工。
- 无硬件阶段默认用 ``NullAudioBackend``；本后端仅在 ``--audio sd`` 且设备就绪时启用。
"""
from __future__ import annotations

import threading

from host.audio.backend import DEFAULT_CHANNELS, DEFAULT_SAMPLE_RATE, Recording
from host.core.errors import ConfigError


def _require(module: str):
    try:
        return __import__(module)
    except ImportError as exc:  # 依赖缺失 → 明确报错，不静默降级
        raise ConfigError(
            f"缺少音频依赖 {module}；请安装 sounddevice/soundfile 或改用 NullAudioBackend"
        ) from exc


class SoundDeviceBackend:
    """基于 PortAudio 的采集/播放实现。"""

    name = "sounddevice"

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: str | int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._lock = threading.Lock()
        self._open = False
        self._playing = False
        self._recording = False

    def open(self) -> None:
        sd = _require("sounddevice")
        with self._lock:
            self._open = True
        # 触发一次查询，尽早暴露「无设备/采样率不支持」问题。
        sd.query_devices(self.device)

    def close(self) -> None:
        sd = _require("sounddevice")
        with self._lock:
            self._open = False
            self._playing = False
            self._recording = False
        sd.stop()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._playing or self._recording

    def record(self, max_seconds: float) -> Recording:
        sd = _require("sounddevice")
        if max_seconds <= 0:
            raise ValueError("max_seconds 必须为正")
        with self._lock:
            if self._playing:
                raise RuntimeError("半双工互斥：正在播放时不能录音")
            self._recording = True
        try:
            frames = int(max_seconds * self.sample_rate)
            buffer = sd.rec(frames, samplerate=self.sample_rate, channels=self.channels,
                            dtype="int16", device=self.device)
            sd.wait()
            data = buffer.tobytes()
        finally:
            with self._lock:
                self._recording = False
        return Recording(
            data=data,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_s=frames / self.sample_rate,
            clipped=_has_clipping(data),
        )

    def play(self, clip, *, volume: float = 1.0) -> None:
        import io  # noqa: PLC0415  （仅播放路径需要）

        sd = _require("sounddevice")
        sf = _require("soundfile")
        with self._lock:
            if self._recording:
                raise RuntimeError("半双工互斥：正在录音时不能播放")
            self._playing = True
        try:
            data, sample_rate = sf.read(io.BytesIO(clip.data), dtype="float32")
            sd.play(data * float(volume), samplerate=sample_rate, device=self.device)
            sd.wait()
        finally:
            with self._lock:
                self._playing = False

    def stop(self) -> None:
        sd = _require("sounddevice")
        with self._lock:
            self._playing = False
            self._recording = False
        sd.stop()


def _has_clipping(pcm: bytes, *, threshold: int = 32760) -> bool:
    """16-bit PCM 削顶检测（任一采样接近满量程即视为削顶）。"""
    import array  # noqa: PLC0415

    samples = array.array("h")
    usable = len(pcm) - (len(pcm) % 2)
    samples.frombytes(pcm[:usable])
    return any(abs(s) >= threshold for s in samples)
