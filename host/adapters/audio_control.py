"""音频操作取消：入队时只置事件，不调用声卡 I/O。"""
from __future__ import annotations

import threading
from dataclasses import replace

from host.core.events import Command, CommandKind

CANCEL_KEY = "_audio_cancel"
START_KINDS = (CommandKind.RECORD_START, CommandKind.PLAY_AUDIO)


class AudioControl:
    """每次录音/播放拥有独立事件，旧停止命令不接触下一次设备操作。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._operations: dict[CommandKind, tuple[int, threading.Event]] = {}
        self._closed = False

    def prepare(self, command: Command) -> Command:
        with self._lock:
            if command.kind in START_KINDS:
                previous = self._operations.get(command.kind)
                if previous is not None:
                    previous[1].set()
                cancel = threading.Event()
                if self._closed:
                    cancel.set()
                self._operations[command.kind] = (command.generation, cancel)
                return replace(command, payload={**command.payload, CANCEL_KEY: cancel})
            start = {
                CommandKind.RECORD_STOP: CommandKind.RECORD_START,
                CommandKind.STOP_AUDIO: CommandKind.PLAY_AUDIO,
            }.get(command.kind)
            operation = self._operations.get(start)
            if operation is not None and operation[0] <= command.generation:
                operation[1].set()
        return command

    @property
    def recording(self) -> tuple[int, threading.Event] | None:
        with self._lock:
            return self._operations.get(CommandKind.RECORD_START)

    def is_current(self, command: Command) -> bool:
        with self._lock:
            operation = self._operations.get(command.kind)
            return not self._closed and (
                operation is None
                or operation == (command.generation, command.payload.get(CANCEL_KEY))
            )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for _, cancel in self._operations.values():
                cancel.set()
