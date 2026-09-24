"""命令路由器：把核心下发的命令**按种类**分发到各领域工作队列（单消费者语义）。

背景（P0-1）：核心（``Engine``）会发出 NET_REQUEST / RECORD_START / RECORD_STOP /
PLAY_AUDIO / STOP_AUDIO / PLAY_ACTION / STOP_MOTION … 等命令，但此前只有一条
``WorkQueue`` 且只有 motion 一条消费路径，其余命令被静默丢弃 → 按键对话永久卡在
THINKING。本模块把「一个队列」拆成三路，使每类命令都有专属消费者线程：

- ``motion``：PLAY_ACTION / STOP_MOTION / STOP / NOTIFY …（由 host-worker 消费）
- ``net``   ：NET_REQUEST（由 net worker 消费）
- ``audio`` ：RECORD_START / RECORD_STOP / PLAY_AUDIO / STOP_AUDIO（由 audio worker 消费）

分层：本模块只依赖 ``host.core``（向下），不含业务逻辑。
"""
from __future__ import annotations

from host.core.bus import WorkQueue
from host.core.events import Command, CommandKind

NET_KINDS: frozenset[CommandKind] = frozenset({CommandKind.NET_REQUEST})

AUDIO_KINDS: frozenset[CommandKind] = frozenset({
    CommandKind.RECORD_START,
    CommandKind.RECORD_STOP,
    CommandKind.PLAY_AUDIO,
    CommandKind.STOP_AUDIO,
})


class CommandRouter:
    """核心 → 领域工作线程的唯一出口；``put`` 即完成分类投递。"""

    def __init__(self) -> None:
        self.motion: WorkQueue[Command] = WorkQueue(name="motion")
        self.net: WorkQueue[Command] = WorkQueue(name="net")
        self.audio: WorkQueue[Command] = WorkQueue(name="audio")

    def put(self, command: Command) -> None:
        """按 ``command.kind`` 投递到对应队列（未知种类归 motion，由 worker 告警）。"""
        kind = command.kind
        if kind in NET_KINDS:
            self.net.put(command)
        elif kind in AUDIO_KINDS:
            self.audio.put(command)
        else:
            self.motion.put(command)

    def close(self) -> None:
        for queue in (self.motion, self.net, self.audio):
            queue.close()
