"""真实后端的回调流取消、部分样本与资源收尾；全程使用 PortAudio 替身。"""
from __future__ import annotations

import io
import struct
import sys
import threading
import wave
from types import SimpleNamespace

import pytest
import soundfile
from host.audio.player import Player
from host.audio.sd_backend import SoundDeviceBackend

from tests.audio_devices import FakePortAudioError, FakeSoundDevice


@pytest.fixture()
def driver(monkeypatch):
    device = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", device)
    yield device
    for stream in device.calls["streams"]:
        stream.close()


def clip(seconds=0.1):
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(struct.pack("<h", 6000) * int(seconds * 16000))
    return SimpleNamespace(data=output.getvalue(), duration_s=seconds)


def background(call):
    result, errors = [], []

    def run():
        try:
            result.append(call())
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result, errors


def first_stream(driver):
    assert driver.stream_created.wait(1)
    stream = driver.calls["streams"][0]
    assert stream.first_block.wait(1), "设备尚未送入首块数据"
    return stream


@pytest.mark.parametrize("stop_way", ["token", "backend"])
def test_stop_preserves_only_the_samples_already_received(driver, stop_way):
    driver.input_gate.clear()
    backend = SoundDeviceBackend()
    cancel = threading.Event()
    thread, result, errors = background(lambda: backend.record(2, cancel=cancel))
    stream = first_stream(driver)
    if stop_way == "token":
        cancel.set()
    else:
        backend.stop()
    thread.join(0.5)
    assert not thread.is_alive(), "取消后仍在等待完整录音时长"
    assert errors == []
    recorded = result[0]
    assert recorded.data == struct.pack("<h", 6000) * 400
    assert recorded.duration_s == 400 / 16000
    assert stream.closed and stream.aborted.is_set()
    assert not backend.busy
    driver.input_gate.set()
    following = backend.record(0.03)
    assert len(following.data) == 480 * 2 and following.duration_s == 0.03


def test_cancelled_before_start_never_opens_streams(driver):
    backend = SoundDeviceBackend()
    cancel = threading.Event()
    cancel.set()
    assert backend.record(15, cancel=cancel).is_empty
    backend.play(clip(), cancel=cancel)
    assert driver.calls["streams"] == []
    assert not backend.busy


def test_playback_interrupt_is_not_reported_as_complete_and_next_play_still_works(driver):
    driver.output_gate.clear()
    backend = SoundDeviceBackend()
    player = Player(backend, level="low")
    cancel = threading.Event()
    thread, result, errors = background(lambda: player.play(clip(1), cancel=cancel))
    stream = first_stream(driver)
    cancel.set()
    thread.join(0.5)
    assert not thread.is_alive() and errors == []
    assert result == [0] and player.last_played_ms == 0
    assert player.progress()["played_ms"] == 0 and not player.playing
    assert stream.closed and not backend.busy
    assert driver.calls["play"][0][0][0, 0] == pytest.approx(6000 / 32768 * 0.3)
    driver.output_gate.set()
    assert player.play(clip(0.05)) == 50
    assert player.progress()["played_ms"] == 50


def test_invalid_playback_does_not_leave_player_or_device_busy(driver):
    backend = SoundDeviceBackend()
    player = Player(backend)
    with pytest.raises(soundfile.LibsndfileError):
        player.play(SimpleNamespace(data=b"not a WAV", duration_s=1))
    assert not player.playing and not backend.busy
    assert player.play(clip(0.05)) == 50


@pytest.mark.parametrize("operation,reason", [("record", "录音设备错误"), ("play", "播放设备错误")])
def test_callback_device_error_closes_stream(driver, operation, reason):
    driver.status = "buffer overflow/underflow"
    backend = SoundDeviceBackend()
    with pytest.raises(RuntimeError, match=reason):
        backend.record(0.1) if operation == "record" else backend.play(clip())
    assert all(stream.closed for stream in driver.calls["streams"])
    assert not backend.busy


def test_start_failure_closes_constructed_stream(driver):
    driver.start_error = True
    backend = SoundDeviceBackend()
    with pytest.raises(FakePortAudioError, match="disappeared"):
        backend.record(0.1)
    assert driver.calls["streams"][0].closed
    assert not backend.busy


def test_cancellation_between_construction_and_start_closes_stream(driver, monkeypatch):
    cancel = threading.Event()
    original = driver.RawInputStream

    def construct(**kwargs):
        stream = original(**kwargs)
        cancel.set()
        return stream

    monkeypatch.setattr(driver, "RawInputStream", construct)
    recorded = SoundDeviceBackend().record(1, cancel=cancel)
    stream = driver.calls["streams"][0]
    assert recorded.is_empty and stream.closed and stream.thread is None


def test_stalled_callback_has_a_finite_timeout_and_closes_stream(driver, monkeypatch):
    driver.input_gate.clear()
    ticks = iter([0.0, 4.0])
    monkeypatch.setattr("host.audio.sd_backend.time",
                        SimpleNamespace(monotonic=lambda: next(ticks)))
    backend = SoundDeviceBackend()
    with pytest.raises(RuntimeError, match="未返回完成信号"):
        backend.record(0.1)
    stream = driver.calls["streams"][0]
    assert stream.closed and stream.aborted.is_set() and not backend.busy
