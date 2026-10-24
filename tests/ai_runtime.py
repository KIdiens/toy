"""可控录放音与工具模型，供 AI 执行链路回放；不接触声卡或云端。"""
from __future__ import annotations

import io
import struct
import threading
import time
import wave

from host.ai.providers import AudioClip, LlmReply
from host.audio.backend import Recording


def wav_bytes(value=2000, frames=3200):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack('<h', value) * frames)
    return output.getvalue()


class ScriptedLlm:
    def __init__(self, calls=(), text='我们依次来。'):
        self.calls, self.text = calls, text
        self.rounds = 0
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()

    def complete(self, messages, tools=None, **kwargs):
        self.rounds += 1
        if self.rounds == 1:
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError('模型测试闸门未释放')
            if self.calls:
                return LlmReply(tool_calls=self.calls)
        return LlmReply(text=self.text)


class TracedTts:
    def __init__(self):
        self.texts = []

    def synthesize(self, text, voice='Cherry', lang=None):
        self.texts.append(text)
        return AudioClip(wav_bytes(value=0, frames=960), 16000)


class TracedAudio:
    name = 'traced-test'
    supports_cancel = True
    sample_rate = 16000
    channels = 1

    def __init__(self):
        self.trace = []
        self.started, self.reply_gate = threading.Event(), threading.Event()
        self.reply_gate.set()
        self.fail_play = False
        self.closed = False
        self.plays = 0

    def open(self):
        self.closed = False

    def stop(self):
        pass  # 只由操作的 cancel 事件结束，避免旧 stop 伤及下一条播放。

    def close(self):
        self.closed = True
        self.reply_gate.set()

    def record(self, seconds, *, cancel=None):
        return Recording(struct.pack('<h', 4000) * 3200, 16000, duration_s=0.2)

    def play(self, clip, *, volume=1, cancel=None):
        self.plays += 1
        number = self.plays
        self.trace.append(('start', number, time.monotonic(), clip.data))
        self.started.set()
        try:
            if self.fail_play:
                raise OSError('测试播放设备已断开')
            deadline = time.monotonic() + 5
            while number == 1 and not self.reply_gate.wait(0.005):
                if cancel is not None and cancel.is_set():
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError('音频测试闸门未释放')
            if cancel is not None:
                cancel.wait(clip.duration_s)
        finally:
            self.trace.append(('end', number, time.monotonic(),
                               cancel is not None and cancel.is_set()))


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return False
