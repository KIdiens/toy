"""入队取消的令牌边界：新一轮不得被旧停止命令或旧录音覆盖。"""
from __future__ import annotations

import threading

import pytest
from host.adapters.audio_control import CANCEL_KEY, AudioControl
from host.adapters.queues import CommandRouter
from host.audio.backend import Recording
from host.core.events import Command, CommandKind


def test_stop_signal_is_sent_before_the_audio_worker_dequeues_it():
    control = AudioControl()
    router = CommandRouter(on_audio=control.prepare)
    router.put(Command(CommandKind.RECORD_START, generation=1))
    first = router.audio.get(timeout=0)
    router.put(Command(CommandKind.RECORD_STOP, generation=1))
    assert first.payload[CANCEL_KEY].is_set()
    assert router.audio.qsize() == 1, "停止信号必须在队列尚未消费时就生效"
    router.put(Command(CommandKind.RECORD_START, generation=2))
    router.audio.get(timeout=0)  # 之后才消费旧停止命令。
    second = router.audio.get(timeout=0)
    assert not second.payload[CANCEL_KEY].is_set()
    assert first.payload[CANCEL_KEY] is not second.payload[CANCEL_KEY]


def test_old_stop_does_not_cancel_a_newer_generation():
    control = AudioControl()
    current = control.prepare(Command(CommandKind.PLAY_AUDIO, generation=5))
    control.prepare(Command(CommandKind.STOP_AUDIO, generation=4))
    assert not current.payload[CANCEL_KEY].is_set()
    control.prepare(Command(CommandKind.STOP_AUDIO, generation=5))
    assert current.payload[CANCEL_KEY].is_set()


def test_playback_stop_does_not_cancel_recording_in_the_same_generation():
    control = AudioControl()
    play = control.prepare(Command(CommandKind.PLAY_AUDIO, generation=1))
    recording = control.prepare(Command(CommandKind.RECORD_START, generation=1))
    control.prepare(Command(CommandKind.STOP_AUDIO, generation=1))
    assert play.payload[CANCEL_KEY].is_set()
    assert not recording.payload[CANCEL_KEY].is_set()


def test_shutdown_cancels_inflight_and_future_operations():
    control = AudioControl()
    current = control.prepare(Command(CommandKind.RECORD_START))
    control.close()
    future = control.prepare(Command(CommandKind.PLAY_AUDIO))
    assert current.payload[CANCEL_KEY].is_set() and future.payload[CANCEL_KEY].is_set()


@pytest.mark.parametrize("previous_ready", [False, True])
def test_net_waits_for_the_queued_recording_not_an_older_same_generation(
    sim_context, previous_ready,
):
    context = sim_context
    first = context.audio_control.prepare(Command(CommandKind.RECORD_START))
    context._rec_started_gen = 0
    if previous_ready:
        context._last_recording = Recording(b"old", 16000)
        context._last_recording_gen = 0
        context._last_recording_cancel = first.payload[CANCEL_KEY]
        context._rec_ready.set()
    following = context.audio_control.prepare(Command(CommandKind.RECORD_START))
    result = []
    waiting = threading.Thread(target=lambda: result.append(context._await_recording(0)),
                               daemon=True)
    waiting.start()
    try:
        waiting.join(0.05)
        assert waiting.is_alive(), "尚未消费新录音命令时，不得返回空值或上一段录音"
        new_recording = Recording(b"new", 16000)
        with context._rec_lock:
            context._last_recording = new_recording
            context._last_recording_gen = 0
            context._last_recording_cancel = following.payload[CANCEL_KEY]
            context._rec_ready.set()
        waiting.join(0.5)
        assert not waiting.is_alive() and result == [new_recording]
    finally:
        context.audio_control.close()
        waiting.join(0.5)
