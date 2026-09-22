"""确定性核心：单线程 actor，消费事件 → 驱动状态机 → 下发命令。

核心线程**从不阻塞在任何 I/O 上**（架构 §3）：网络/串口/音频都在自己的线程里
执行，结果只以事件回投本线程的队列。这保证了「网络注入 10 秒假请求」时串口与
按键仍然即时响应（AC-22）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import (
    BUTTON_DISCARD,
    BUTTON_NOOP,
    SessionStateMachine,
    State,
    Trigger,
    trigger_for_button,
)

EVENT_TRIGGERS: dict[EventKind, Trigger] = {
    EventKind.BOOT_OK: Trigger.BOOT_OK,
    EventKind.BOOT_FAULT: Trigger.BOOT_FAULT,
    EventKind.REC_STOP: Trigger.REC_STOP,
    EventKind.REC_CANCEL: Trigger.REC_CANCEL,
    EventKind.AI_DONE: Trigger.AI_DONE,
    EventKind.AI_FAIL: Trigger.AI_FAIL,
    EventKind.PLAY_DONE: Trigger.PLAY_DONE,
    EventKind.PERFORM_DONE: Trigger.PERFORM_DONE,
    EventKind.SAFETY_TRIP: Trigger.SAFETY_TRIP,
    EventKind.FAULT_CLEAR: Trigger.FAULT_CLEAR,
    EventKind.IDLE_TICK: Trigger.IDLE_TICK,
}

ENTER_COMMANDS: dict[State, tuple[CommandKind, ...]] = {
    State.RECORDING: (CommandKind.RECORD_START,),
    State.THINKING: (CommandKind.NET_REQUEST,),  # 录音结束 → 交给 net 线程做 ASR/LLM（核心不阻塞）
    State.PERFORMING: (CommandKind.PLAY_ACTION,),
    State.IDLE: (CommandKind.STOP_AUDIO, CommandKind.STOP_MOTION),
}

EXIT_COMMANDS: dict[State, tuple[CommandKind, ...]] = {
    State.RECORDING: (CommandKind.RECORD_STOP,),
    State.SPEAKING: (CommandKind.STOP_AUDIO,),
    State.PERFORMING: (CommandKind.STOP_MOTION,),
}

Dispatcher = Callable[["Engine", Event], list[Command]]


def default_dispatcher(engine: Engine, event: Event) -> list[Command]:
    """默认事件路由：按键 → 状态机；MCU 故障 → 抢占式锁定；迟到回包 → 丢弃。"""
    machine = engine.state_machine
    kind = event.kind

    if kind is EventKind.SHUTDOWN:
        return []
    if kind is EventKind.BUTTON:
        action = trigger_for_button(machine.state, int(event.payload.get("n", 0)))
        if action in (BUTTON_NOOP, BUTTON_DISCARD):
            return []
        return engine.apply(action, event.payload, event.generation)
    if kind in (EventKind.MCU_FAULT, EventKind.MCU_ERROR):
        return engine.apply(Trigger.SAFETY_TRIP, event.payload, event.generation)
    if kind is EventKind.MCU_STATE:
        line = event.payload.get("state")
        if line and (line[1] == 0 or line[2] == 0):
            return engine.apply(Trigger.SAFETY_TRIP, event.payload, event.generation)
        return []
    if kind is EventKind.NET_RESULT and event.generation != machine.generation:
        return []  # 换角色/取消后的迟到回包一律丢弃（AC-15）
    trigger = EVENT_TRIGGERS.get(kind)
    if trigger is None:
        return []
    return engine.apply(trigger, event.payload, event.generation)


class Engine:
    """单线程确定性核心。``step`` 处理一个事件；``run_forever`` 直到 SHUTDOWN。"""

    def __init__(
        self,
        bus,
        out,
        *,
        state_machine: SessionStateMachine | None = None,
        clock=None,
        dispatcher: Dispatcher | None = None,
        logger=None,
        idle_timeout: float = 0.1,
    ) -> None:
        self.bus = bus
        self.out = out
        self.state_machine = state_machine or SessionStateMachine(logger=logger)
        self.clock = clock
        self.logger = logger
        self._dispatcher = dispatcher or default_dispatcher
        self._idle_timeout = idle_timeout
        self._stopped = False
        self.processed = 0

    def submit(self, event: Event) -> None:
        """任意线程可调：把事件投进核心队列。"""
        self.bus.publish(event)

    def apply(
        self, trigger: Trigger | str, payload: Mapping | None = None, generation: int = 0
    ) -> list[Command]:
        """驱动状态机并按迁移发出命令；非法迁移返回空列表（不抛异常）。"""
        machine = self.state_machine
        result = machine.fire(trigger, context=payload)
        if not result.ok:
            return []
        commands = [
            Command(kind=kind, payload=dict(payload or {}), generation=machine.generation)
            for kind in EXIT_COMMANDS.get(result.source, ())
        ]
        commands += [
            Command(kind=kind, payload=dict(payload or {}), generation=machine.generation)
            for kind in ENTER_COMMANDS.get(result.dest, ())
        ]
        commands.append(
            Command(
                kind=CommandKind.NOTIFY,
                payload={"state": result.dest.value, "trigger": result.trigger.value},
                generation=machine.generation,
            )
        )
        return commands

    def step(self, timeout: float | None = None) -> bool:
        """处理一个事件；返回 ``False`` 表示收到 SHUTDOWN。"""
        event = self.bus.get(timeout=self._idle_timeout if timeout is None else timeout)
        if event is None:
            return True
        if event.kind is EventKind.SHUTDOWN:
            self._stopped = True
            return False
        self.processed += 1
        if self.logger is not None:
            self.logger.debug("core <- %s", event.kind.value)
        for command in self._dispatcher(self, event):
            self.out.put(command)
        return True

    def run_forever(self) -> None:
        while not self._stopped and self.step():
            pass

    def stop(self) -> None:
        self.bus.shutdown()
