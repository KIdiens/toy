"""PortAudio 回调与流生命周期替身；测试不会打开本机声卡。"""
from __future__ import annotations

import struct
import threading

import numpy as np


class FakePortAudioError(Exception):
    pass


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class FakeAudioStream:
    def __init__(self, driver, *, recording, kwargs):
        self.driver = driver
        self.recording = recording
        self.kwargs = kwargs
        self.first_block = threading.Event()
        self.aborted = threading.Event()
        self.closed = False
        self.thread = None

    def start(self):
        if self.driver.start_error:
            raise FakePortAudioError("device disappeared")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        frames = 400
        try:
            while not self.aborted.is_set():
                samples = (struct.pack("<h", self.driver.input_amplitude)
                           * frames * self.kwargs["channels"]
                           if self.recording else
                           np.zeros((frames, self.kwargs["channels"]), dtype="float32"))
                try:
                    self.kwargs["callback"](samples, frames, None, self.driver.status)
                except CallbackStop:
                    if not self.recording:
                        self.driver.calls["play"].append((samples.copy(), self.kwargs))
                    self.first_block.set()
                    break
                except CallbackAbort:
                    break
                if not self.recording:
                    self.driver.calls["play"].append((samples.copy(), self.kwargs))
                self.first_block.set()
                gate = self.driver.input_gate if self.recording else self.driver.output_gate
                while not gate.is_set() and not self.aborted.wait(0.001):
                    pass
        finally:
            self.kwargs["finished_callback"]()

    def abort(self):
        self.aborted.set()

    def close(self):
        self.aborted.set()
        if self.thread is not None:
            self.thread.join(1)
            assert not self.thread.is_alive(), "设备替身回调线程泄漏"
        self.closed = True


class FakeSoundDevice:
    PortAudioError = FakePortAudioError
    CallbackStop = CallbackStop
    CallbackAbort = CallbackAbort

    def __init__(self):
        self.calls = {"input": [], "output": [], "record": [], "play": [], "streams": []}
        self.input_gate = threading.Event()
        self.output_gate = threading.Event()
        self.input_gate.set()
        self.output_gate.set()
        self.start_error = False
        self.status = None
        self.stream_created = threading.Event()
        self.input_created = threading.Event()
        self.output_created = threading.Event()
        self.input_amplitude = 6000

    def check_input_settings(self, **kwargs):
        self.calls["input"].append(kwargs)

    def check_output_settings(self, **kwargs):
        self.calls["output"].append(kwargs)

    def RawInputStream(self, **kwargs):
        self.calls["record"].append(kwargs)
        return self._stream(recording=True, kwargs=kwargs)

    def OutputStream(self, **kwargs):
        return self._stream(recording=False, kwargs=kwargs)

    def _stream(self, *, recording, kwargs):
        stream = FakeAudioStream(self, recording=recording, kwargs=kwargs)
        self.calls["streams"].append(stream)
        self.stream_created.set()
        (self.input_created if recording else self.output_created).set()
        return stream
