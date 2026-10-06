"""确定性核心：单线程 actor，消费事件 → 驱动状态机 → 下发命令。

核心线程**从不阻塞在任何 I/O 上**（架构 §3）：网络/串口/音频都在自己的线程里
执行，结果只以事件回投本线程的队列。这保证了「网络注入 10 秒假请求」时串口与
按键仍然即时响应（AC-22）。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

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
    State.SPEAKING: (CommandKind.PLAY_AUDIO,),  # 回复就绪 → 交给 audio 线程播放（P0-1）
    State.PERFORMING: (CommandKind.PLAY_ACTION,),
    State.IDLE: (CommandKind.STOP_AUDIO, CommandKind.STOP_MOTION),
}

EXIT_COMMANDS: dict[State, tuple[CommandKind, ...]] = {
    State.RECORDING: (CommandKind.RECORD_STOP,),
    State.SPEAKING: (CommandKind.STOP_AUDIO,),
    State.PERFORMING: (CommandKind.STOP_MOTION,),
}

Dispatcher = Callable[["Engine", Event], list[Command]]

# 表演键的**语义**标记：由 core 写进 PLAY_ACTION 的 payload，adapter 据此解析出真实动作。
#
# 为什么标记在 core、解析在 adapter：core 是确定性单线程 actor，**不做任何 I/O**
# （模块 docstring 与架构 §3），既不 import `roles` 也不碰文件系统，拿不到当前
# role_id 与动作 JSON 本体。因此 core 只能表达「这一下按的是表演键，语义是打招呼」，
# 由持有 ``roles`` 的 adapter 把它翻译成 ``{"action": <动作本体>, "role_id": ...}``。
# 这与音量键的 ``VOLUME_STEP`` 是同一形状的分工：core 表达意图，adapters 执行效果。
#
# 载荷里刻意**不含**具体动作 id：动作从哪个文件解析属于装配知识（角色包可换人），
# 写进 core 会让它依赖 content 布局。
GREET_INTENT = "greet"

# 「跨会话的异步结果」类事件：由领域线程异步产生、且**可能跨越一次取消/换角色**才回到
# 核心，因此必须核对 ``generation``，不符一律丢弃（AC-15）。这条边界两侧都各有一个反例，
# 既不能放宽也不能收紧（回归见 tests/unit/test_generation_guard.py）：
#
# - 放宽（连 AI_DONE / AI_FAIL 也收）→ 上一轮的回复会在新会话里被说出来；
# - 收紧（把 PLAY_DONE / PERFORM_DONE 也算进来）→ 它们是**本地命令回执**而非跨会话结果，
#   丢弃即永久驻留：``switch_active_role`` 允许在 SPEAKING 期间换角色并令 generation +1，
#   紧接着到来的 PLAY_DONE 会被误丢，状态机就再收不到离开 SPEAKING 的信号。
STALE_SENSITIVE: frozenset[EventKind] = frozenset({
    EventKind.NET_RESULT,
    EventKind.AI_DONE,
    EventKind.AI_FAIL,
    EventKind.REC_STOP,  # 上一轮录音的迟到收尾，不得把新一轮录音提前打断成 THINKING
})


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
        commands = engine.apply(action, event.payload)
        if action == Trigger.VOLUME_PRESS.value:
            # 音量键是**内部迁移**（不改状态、不产生 EXIT/ENTER 命令），它的实际效果是
            # 「音量档位进一档」。这条接线此前完全缺失——`Player.cycle_level()` 在生产代码里
            # 零调用者，于是音量键按下去什么都不会发生。
            commands.append(
                Command(kind=CommandKind.VOLUME_STEP, payload=dict(event.payload or {}),
                        generation=machine.generation)
            )
        if action == Trigger.PERFORM_PRESS.value:
            # 表演键 = 打招呼（产品定义）。此前 PLAY_ACTION 带着空载荷下发，
            # adapter 只能按「无动作」收尾，于是按下去是一个动作都不做的状态往返。
            # 这里只打语义标记，动作本体由 adapter 侧解析（见 GREET_INTENT 的说明）。
            commands = [_mark_greet(c) for c in commands]
        return commands
    if kind in (EventKind.MCU_FAULT, EventKind.MCU_ERROR):
        return engine.apply(Trigger.SAFETY_TRIP, event.payload)
    if kind is EventKind.MCU_STATE:
        line = event.payload.get("state")
        if line and (line[1] == 0 or line[2] == 0):
            return engine.apply(Trigger.SAFETY_TRIP, event.payload)
        return []
    if kind in STALE_SENSITIVE and event.generation != machine.generation:
        return []  # 换角色/取消后的迟到回包一律丢弃（AC-15）
    trigger = EVENT_TRIGGERS.get(kind)
    if trigger is None:
        return []
    return engine.apply(trigger, event.payload)


def _mark_greet(command: Command) -> Command:
    """给表演键下发的 ``PLAY_ACTION`` 打上打招呼语义标记（其余命令原样返回）。

    只改载荷、不改代数与种类：``_run_play`` 的迟到丢弃（``ports.play_action`` 走的是
    同一份判定）仍按 ``generation`` 生效。
    """
    if command.kind is not CommandKind.PLAY_ACTION:
        return command
    return replace(command, payload={**dict(command.payload or {}),
                                     "perform_intent": GREET_INTENT})


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
        self, trigger: Trigger | str, payload: Mapping | None = None
    ) -> list[Command]:
        """驱动状态机并按迁移发出命令；非法迁移返回空列表（不抛异常）。

        **代数纪律**：``fire()`` 会在「进入 IDLE」时把 ``session_generation`` 加一，所以
        EXIT 命令必须携带**迁移之前**的代数——它描述的是「正在离开的那个会话」。若误用迁移
        后的代数，取消录音时下游 ``_finish_recording`` 会判代数不符而走兜底，凭空多录一段。
        ENTER / NOTIFY 描述的才是「刚进入的新会话」，用迁移后的代数。

        **这里没有``generation`` 形参（曾有过一个自始未被引用的死参，已删）**：
        命令代数一律现取自 ``state_machine``。陈旧判定属于**事件路由**的职责，
        发生在 ``default_dispatcher``（``kind in STALE_SENSITIVE`` 分支）——
        早于本函数。把代数当形参传进来，等于让调用方以为「传了就会按我传的算」，
        而实现在``or`` 兜底下会让「不传」与「传 0」行为不同、却无人察觉。
        顺带核实过：即使把形参真的接上（``generation or machine.generation``），
        当前 4 个 ``default_dispatcher`` 调用点传的 ``event.generation`` 在此本就
        等于 ``machine.generation``（陈旧事件已被上游丢弃），全量测试同样全绿——
        所以删它零行为变化，纯粹是移除一个会误导读者的接口。
        """
        machine = self.state_machine
        pre_generation = machine.generation
        result = machine.fire(trigger, context=payload)
        if not result.ok:
            return []
        notify = Command(
            kind=CommandKind.NOTIFY,
            payload={"state": result.dest.value, "trigger": result.trigger.value},
            generation=machine.generation,
        )
        if not result.changed:
            # 自环迁移（音量键：IDLE/THINKING/SPEAKING/PERFORMING 各自 → 自身）语义是
            # 「内部意图，不改变状态」（state_machine 模块注释已如此声明），因此**不得**
            # 下发 EXIT/ENTER 命令。否则音量键会：重发 NET_REQUEST（重复 ASR/LLM 调用与计费）、
            # 重发 PLAY_AUDIO（无 clip → 立刻 PLAY_DONE → 截断正在说的话）、
            # 重发 PLAY_ACTION（掐掉正在跑的动作）。
            return [notify]
        commands = [
            Command(kind=kind, payload=dict(payload or {}), generation=pre_generation)
            for kind in EXIT_COMMANDS.get(result.source, ())
        ]
        commands += [
            Command(kind=kind, payload=dict(payload or {}), generation=machine.generation)
            for kind in ENTER_COMMANDS.get(result.dest, ())
        ]
        commands.append(notify)
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
