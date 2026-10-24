"""按键到阻塞录放音的取消回归：不访问真实声卡。"""
from __future__ import annotations

import io
import struct
import sys
import threading
import time
import wave
from contextlib import contextmanager
from types import SimpleNamespace

from host.adapters.runtime import build_context
from host.audio.backend import Recording
from host.config.settings import load_settings
from host.core.events import Command, CommandKind
from host.core.state_machine import State

from tests.audio_devices import FakeSoundDevice


class BlockingAudio:
    name = "blocking-test"
    supports_cancel = True

    def __init__(self):
        self.force_release = threading.Event()
        self.record_started = threading.Event()
        self.record_finished = threading.Event()
        self.play_started = threading.Event()
        self.play_finished = threading.Event()
        self.record_calls = 0

    def open(self):
        pass

    def close(self):
        self.force_release.set()

    def stop(self):
        pass  # 普通停止不能代替操作自己的取消令牌。

    def _wait(self, cancel):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.force_release.wait(0.005):
            if cancel is not None and cancel.is_set():
                return

    def record(self, seconds, *, cancel=None):
        self.record_calls += 1
        self.record_started.set()
        self._wait(cancel)
        self.record_finished.set()
        return Recording(struct.pack("<h", 4000) * 8000, 16000, duration_s=0.5)

    def play(self, clip, *, volume=1, cancel=None):
        self.play_started.set()
        self._wait(cancel)
        self.play_finished.set()


@contextmanager
def running_audio(tmp_path, *, real_backend=False):
    context = build_context(load_settings({
        "DB_PATH": str(tmp_path / "toy.db"), "AUDIO_BACKEND": "sd" if real_backend else "null",
    }), simulate=True)
    device = context.player.backend if real_backend else BlockingAudio()
    if not real_backend:
        context.player.backend = context.recorder.backend = device
    stop = threading.Event()
    context.worker_running = True
    context.start_workers()
    worker = threading.Thread(target=context.run_forever, args=(stop,), daemon=True)
    context.register_motion_thread(worker)
    worker.start()
    try:
        yield context, device
    finally:
        if isinstance(device, BlockingAudio):
            device.force_release.set()
        stop.set()
        context.close()
        worker.join(1)
        assert not worker.is_alive()


def test_second_dialog_press_stops_inflight_recording(tmp_path):
    with running_audio(tmp_path) as (context, device):
        assert context.press_button(1)["state"] == "RECORDING"
        assert device.record_started.wait(1), "录音尚未实际开始"
        start = time.monotonic()
        assert context.press_button(1)["state"] == "THINKING"
        assert time.monotonic() - start < 0.2, "按键处理被声卡 I/O 阻塞"
        assert device.record_finished.wait(0.5), "结束录音命令排在阻塞录音后，未及时生效"
        assert context._rec_ready.wait(0.5), "停止后未保留已采集的语音"
        assert context._last_recording.data == struct.pack("<h", 4000) * 8000


def test_dialog_press_interrupts_playback_before_starting_recording(tmp_path):
    with running_audio(tmp_path) as (context, device):
        context.state_machine.reset(initial=State.SPEAKING)
        context._router.put(Command(CommandKind.PLAY_AUDIO,
                                    {"clip": SimpleNamespace(duration_s=2)},
                                    context.state_machine.generation))
        assert device.play_started.wait(1), "播放尚未实际开始"
        assert context.press_button(1)["state"] == "RECORDING"
        assert device.play_finished.wait(0.5), "停止播放命令排在阻塞播放后，未及时生效"
        assert device.record_started.wait(0.5), "播放停止后没有开始下一轮录音"
        context.drain()
        assert context.state_machine.state is State.RECORDING, "旧播放完成事件结束了新录音"


def test_shutdown_unblocks_recording_before_joining_audio_thread(tmp_path):
    with running_audio(tmp_path) as (context, device):
        context.press_button(1)
        assert device.record_started.wait(1)
        threads = list(context._domain_threads)
        start = time.monotonic()
        context.close()
        assert time.monotonic() - start < 0.5, "先 join 再取消会等满关停超时"
        assert device.record_finished.is_set()
        assert all(not thread.is_alive() for thread in threads)


def test_two_rounds_through_real_audio_adapter_do_not_reuse_old_samples(tmp_path, monkeypatch):
    driver = FakeSoundDevice()
    driver.input_gate.clear()
    driver.output_gate.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", driver)
    captured = []
    with running_audio(tmp_path, real_backend=True) as (context, _backend):
        def transcribe(wav, lang=None):
            with wave.open(io.BytesIO(wav), "rb") as handle:
                captured.append((handle.getframerate(), handle.getnframes(),
                                 handle.readframes(handle.getnframes())))
            return "你好"

        context.router.asr.transcribe = transcribe
        context.press_button(1)
        assert driver.input_created.wait(1)
        assert driver.calls["streams"][0].first_block.wait(1)
        assert context.press_button(1)["state"] == "THINKING"
        assert driver.output_created.wait(1), "第一次录音未经过 ASR/TTS 形成播放"
        assert driver.calls["streams"][-1].first_block.wait(1)

        driver.input_created.clear()
        driver.output_created.clear()
        driver.input_amplitude = 7000
        assert context.press_button(1)["state"] == "RECORDING"
        assert driver.input_created.wait(0.5), "打断旧播放后未打开新录音流"
        following = driver.calls["streams"][-1]
        assert following.first_block.wait(1) and not following.closed
        context.drain()
        assert context.state_machine.state is State.RECORDING
        assert context.press_button(1)["state"] == "THINKING"
        assert driver.output_created.wait(1)
        assert captured == [
            (16000, 400, struct.pack("<h", 6000) * 400),
            (16000, 400, struct.pack("<h", 7000) * 400),
        ]
    assert all(stream.closed for stream in driver.calls["streams"])
