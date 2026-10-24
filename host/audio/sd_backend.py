"""真实声卡后端：``sounddevice`` + ``soundfile``（ADR-008）。

- **延迟导入**：本模块在缺 ``sounddevice``/``soundfile`` 的机器上仍可被 import；
  只有真正调用 ``open``/``record``/``play`` 时才要求依赖存在，缺失则抛 ``ConfigError``
  （不吞异常、给出可执行的安装指引）。
- 同一 PortAudio API 覆盖 Windows WASAPI 与树莓派 ALSA；全局单实例、半双工。
- 无硬件阶段默认用 ``NullAudioBackend``；本后端仅在 ``--audio sd`` 且设备就绪时启用。
"""
from __future__ import annotations

import io
import threading
import time

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
    supports_cancel = True

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: str | int | None = None,
        input_device: str | int | None = None,
        output_device: str | int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.input_device = device if input_device is None else input_device
        self.output_device = device if output_device is None else output_device
        self._lock = threading.Lock()
        self._open = False
        self._playing = False
        self._recording = False
        self._active_cancel: threading.Event | None = None

    def open(self) -> None:
        sd = _require("sounddevice")
        _require("soundfile")
        with self._lock:
            if self._open:
                return
        try:
            sd.check_input_settings(device=self.input_device, channels=self.channels,
                                    dtype="int16", samplerate=self.sample_rate)
            sd.check_output_settings(device=self.output_device, channels=self.channels,
                                     dtype="float32", samplerate=self.sample_rate)
        except (ValueError, sd.PortAudioError) as exc:
            raise ConfigError(
                "真实音频设备不可用：请检查 AUDIO_INPUT_DEVICE / AUDIO_OUTPUT_DEVICE "
                f"和 AUDIO_SAMPLE_RATE；也可用 --audio null 运行仿真。原因：{exc}"
            ) from exc
        with self._lock:
            self._open = True

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._open = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._playing or self._recording

    def _begin(self, *, recording: bool, cancel: threading.Event | None) -> threading.Event:
        with self._lock:
            if self._playing or self._recording:
                raise RuntimeError("半双工互斥：已有录音或播放在执行")
            cancel = cancel if cancel is not None else threading.Event()
            self._active_cancel = cancel
            self._recording = recording
            self._playing = not recording
            return cancel

    def _end(self, cancel: threading.Event) -> None:
        with self._lock:
            if self._active_cancel is cancel:
                self._active_cancel = None
                self._playing = self._recording = False

    def record(self, max_seconds: float, *, cancel: threading.Event | None = None) -> Recording:
        sd = _require("sounddevice")
        if max_seconds <= 0:
            raise ValueError("max_seconds 必须为正")
        cancel = self._begin(recording=True, cancel=cancel)
        limit = int(max_seconds * self.sample_rate)
        chunks: list[bytes] = []
        received = 0
        errors: list[str] = []
        done = threading.Event()

        def collect(indata, frames, _time_info, status):
            nonlocal received
            if cancel.is_set():
                raise sd.CallbackAbort
            if status:
                errors.append(str(status))
                raise sd.CallbackAbort
            count = min(frames, limit - received)
            chunks.append(bytes(indata)[:count * self.channels * 2])
            received += count
            if received >= limit:
                raise sd.CallbackStop

        try:
            if limit > 0 and not cancel.is_set():
                stream = sd.RawInputStream(
                    samplerate=self.sample_rate, channels=self.channels, dtype="int16",
                    device=self.input_device, callback=collect, finished_callback=done.set,
                )
                _run_stream(stream, done, cancel, max_seconds)
            if errors:
                raise RuntimeError("录音设备错误：" + "; ".join(errors))
        finally:
            self._end(cancel)
        data = b"".join(chunks)
        return Recording(
            data=data,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_s=received / self.sample_rate,
            clipped=_has_clipping(data),
        )

    def play(self, clip, *, volume: float = 1.0, cancel: threading.Event | None = None) -> None:
        sd = _require("sounddevice")
        sf = _require("soundfile")
        cancel = self._begin(recording=False, cancel=cancel)
        try:
            if cancel.is_set():
                return
            data, sample_rate = sf.read(io.BytesIO(clip.data), dtype="float32", always_2d=True)
            data *= float(volume)
            position = 0
            done = threading.Event()
            errors: list[str] = []

            def produce(outdata, frames, _time_info, status):
                nonlocal position
                outdata.fill(0)
                if cancel.is_set():
                    raise sd.CallbackAbort
                if status:
                    errors.append(str(status))
                    raise sd.CallbackAbort
                count = min(frames, len(data) - position)
                outdata[:count] = data[position:position + count]
                position += count
                if position >= len(data):
                    raise sd.CallbackStop

            if len(data):
                stream = sd.OutputStream(
                    samplerate=sample_rate, channels=data.shape[1], dtype="float32",
                    device=self.output_device, callback=produce, finished_callback=done.set,
                )
                _run_stream(stream, done, cancel, len(data) / sample_rate)
            if errors:
                raise RuntimeError("播放设备错误：" + "; ".join(errors))
        finally:
            self._end(cancel)

    def stop(self) -> None:
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()  # 非阻塞；流由执行录放音的线程中止并关闭。


def _run_stream(stream, done: threading.Event, cancel: threading.Event, seconds: float) -> None:
    try:
        if cancel.is_set():
            return
        deadline = time.monotonic() + seconds + 2.0
        stream.start()
        while not done.wait(0.01):
            if cancel.is_set():
                stream.abort()
                break
            if time.monotonic() >= deadline:
                stream.abort()
                raise RuntimeError("音频设备未返回完成信号，已中止本次录放音")
    finally:
        stream.close()  # 包括 start() 失败、尚未启动就取消的路径。


def _has_clipping(pcm: bytes, *, threshold: int = 32760) -> bool:
    """16-bit PCM 削顶检测（任一采样接近满量程即视为削顶）。"""
    import array  # noqa: PLC0415

    samples = array.array("h")
    usable = len(pcm) - (len(pcm) % 2)
    samples.frombytes(pcm[:usable])
    return any(abs(s) >= threshold for s in samples)
