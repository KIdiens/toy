"""设备错误必须结束失败轮次、阻止坏录音进入 ASR，并允许下一轮重试。"""
from __future__ import annotations

import io
import sys
import threading
import time
import wave
from types import SimpleNamespace

from host.core.events import Command, CommandKind
from host.core.state_machine import State

from tests.audio_devices import FakePortAudioError, FakeSoundDevice
from tests.integration.test_audio_interrupt import running_audio


def wait_idle(context):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if context.state_machine.state is State.IDLE:
            return True
        time.sleep(0.005)
    return False


def observe_asr(context):
    captured = []

    def transcribe(wav, lang=None):
        captured.append(None)  # 进入语音服务即计数，坏 WAV 的解析异常不能掩盖调用。
        with wave.open(io.BytesIO(wav), "rb") as handle:
            captured[-1] = handle.getnframes()
        return "你好"

    context.router.asr.transcribe = transcribe
    return captured


def successful_retry(context, driver):
    driver.input_created.clear()
    driver.output_created.clear()
    driver.input_gate.clear()
    assert context.press_button(1)["state"] == "RECORDING"
    assert driver.input_created.wait(1)
    assert driver.calls["streams"][-1].first_block.wait(1)
    assert context.press_button(1)["state"] == "THINKING"
    assert driver.output_created.wait(1)
    assert wait_idle(context), "设备恢复后重试未完成"


def test_recording_device_failure_recovers_without_cloud_call_and_allows_retry(
    tmp_path, monkeypatch,
):
    driver = FakeSoundDevice()
    driver.start_error = True
    monkeypatch.setitem(sys.modules, "sounddevice", driver)
    with running_audio(tmp_path, real_backend=True) as (context, _backend):
        captured = observe_asr(context)
        generation = context.state_machine.generation
        context.press_button(1)
        assert wait_idle(context), "录音启动失败后仍卡在 RECORDING"
        assert context._rec_ready.is_set(), "失败没有唤醒等待录音结果的线程"
        assert captured == [], "失败录音进入了语音服务"
        assert context.state_machine.history[-1].trigger.value == "AUDIO_FAIL"
        assert context.worker_faults >= 1 and context.worker_alive()
        driver.start_error = False
        successful_retry(context, driver)
        assert captured == [400]
        assert context._recording_error is None
        assert context.state_machine.generation == generation + 2


def test_playback_device_failure_recovers_without_claiming_success_and_allows_retry(
    tmp_path, monkeypatch,
):
    driver = FakeSoundDevice()
    driver.input_gate.clear()
    monkeypatch.setitem(sys.modules, "sounddevice", driver)
    original = driver.OutputStream

    def unavailable(**kwargs):
        raise FakePortAudioError("speaker disappeared")

    monkeypatch.setattr(driver, "OutputStream", unavailable)
    with running_audio(tmp_path, real_backend=True) as (context, backend):
        captured = observe_asr(context)
        context.press_button(1)
        assert driver.input_created.wait(1)
        assert driver.calls["streams"][0].first_block.wait(1)
        context.press_button(1)
        assert wait_idle(context), "播放设备失败后仍卡在 SPEAKING"
        assert context.state_machine.history[-1].trigger.value == "AUDIO_FAIL"
        assert not backend.busy and not context.player.playing
        assert context.worker_faults >= 1 and context.worker_alive()
        monkeypatch.setattr(driver, "OutputStream", original)
        successful_retry(context, driver)
        assert captured == [400, 400]


def test_capture_failure_after_stop_wakes_net_thread_and_never_calls_asr(tmp_path):
    with running_audio(tmp_path) as (context, device):
        captured = observe_asr(context)

        def fail_capture(seconds, *, cancel=None):
            device.record_started.set()
            device._wait(cancel)
            raise RuntimeError("microphone disconnected at stop")

        device.record = fail_capture
        context.press_button(1)
        assert device.record_started.wait(1)
        assert context.press_button(1)["state"] == "THINKING"
        assert context._rec_ready.wait(0.5), "录音异常没有发布失败结果，网络线程仍在等"
        assert wait_idle(context), "失败后未退出 THINKING"
        assert captured == []
        assert context.worker_alive()


def test_current_playback_failure_recovers_even_after_role_change(tmp_path):
    with running_audio(tmp_path) as (context, device):
        fail = threading.Event()

        def play(clip, *, volume=1, cancel=None):
            device.play_started.set()
            assert fail.wait(1)
            raise RuntimeError("speaker disconnected after role change")

        device.play = play
        context.state_machine.reset(initial=State.SPEAKING)
        context._router.put(Command(CommandKind.PLAY_AUDIO,
                                    {"clip": SimpleNamespace(duration_s=1)},
                                    context.state_machine.generation))
        assert device.play_started.wait(1)
        context.switch_active_role(3)
        generation = context.state_machine.generation
        assert context.state_machine.state is State.SPEAKING
        fail.set()
        assert wait_idle(context), "当前播放失败回执被角色切换后的代数守卫丢弃"
        assert context.state_machine.generation == generation + 1
        assert context.state_machine.history[-1].trigger.value == "AUDIO_FAIL"


def test_late_interrupted_playback_failure_does_not_end_new_recording(tmp_path):
    with running_audio(tmp_path) as (context, device):
        def play(clip, *, volume=1, cancel=None):
            device.play_started.set()
            device._wait(cancel)
            raise RuntimeError("old playback failed during cancellation")

        device.play = play
        context.state_machine.reset(initial=State.SPEAKING)
        context._router.put(Command(CommandKind.PLAY_AUDIO,
                                    {"clip": SimpleNamespace(duration_s=1)},
                                    context.state_machine.generation))
        assert device.play_started.wait(1)
        assert context.press_button(1)["state"] == "RECORDING"
        assert device.record_started.wait(1), "旧播放异常阻止了新录音启动"
        context.drain()
        assert context.worker_faults >= 1, "测试未触发旧播放异常"
        assert context.state_machine.state is State.RECORDING and context.worker_alive()
