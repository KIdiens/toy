"""执行循环：**工作队列（运动）** 与 **事件总线（状态机）** 的消费与锁纪律。

线程模型（architecture §3 的落地）：

- HTTP 线程只做「校验 → 入队 / 投事件」，**不阻塞在任何 I/O 上**；
- ``run_forever`` 由 ``main`` 在**唯一** worker 线程里跑：先执行运动命令，再推进状态机；
- ``_run_play`` 里的 ``motion.play`` 长耗时调用**不持锁**，使急停 / 拔出注入即时生效（AC-22）；
- ``self._lock``（RLock）保证「同一时刻只有一个线程在改状态机」，即使 worker 与 HTTP 并存
  也不会破坏核心的单线程不变量。
"""
from __future__ import annotations

from dataclasses import replace

from host.adapters.motion_execution import MotionExecutionMixin, fault_code
from host.core.engine import GREET_INTENT
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import State, Trigger

DRAIN_LIMIT = 2000
WORKER_IDLE_S = 0.005
# NOTIFY 仅通知；停止/替换先使播放令牌失效，MotionClient 的轮询取消检查负责
# 中断在途动作并在 finally 执行 STOP。队列里的 STOP 不再等待旧时间线结束才生效。
MOTION_SILENT_KINDS: frozenset[CommandKind] = frozenset({
    CommandKind.NOTIFY,
    CommandKind.STOP,
    CommandKind.STOP_MOTION,
})

# 物理表演键默认播放的动作 id（物理表演键 = 打招呼）。
#
# **不复用 wave/nod 的实测依据**：`content/roles/*/role.json` 的 actions 白名单里
# wave 只有 role 1/2、nod 只有 role 1/3 —— 没有任何一个现有 id 被三个角色共享，
# 复用单个现有 id 必然有角色在自己的包里找不到动作。新建 greet 后三个角色各自
# 都有同名同义的动作，按键行为不再随角色包内容漂移。
GREET_ACTION_ID = "greet"


class ExecutionMixin(MotionExecutionMixin):
    """依赖宿主提供 ``work`` / ``bus`` / ``engine`` / ``state_machine`` /
    ``motion`` / ``sim`` / ``clock`` / ``player`` / ``_lock`` / ``worker_running``。
    """

    # ---------- 上电 / 命令下发 ----------
    def boot(self) -> dict:
        """上电序列：MCU 上电 → ``BOOT_OK`` → IDLE（装配后调用一次）。"""
        with self._lock:
            if self.sim is not None:
                self.sim.power_on()
            self._deliver(self.engine.apply(
                Trigger.BOOT_OK, {"mcu_ready": True, "role_stable": True}
            ))
            return self.status_snapshot()

    def _deliver(self, commands) -> None:
        """把核心命令经路由器投进对应领域工作队列（调用方通常已持锁）。"""
        for command in commands:
            self._router.put(command)

    # ---------- 播放令牌（陈旧判定的唯一依据）----------
    def _begin_play(self) -> int:
        """分配并**立即激活**一个新播放令牌；此前所有播放（含在途命令）随即失效。

        调用方必须持 ``self._lock``。语义等价于「我要开始一次播放，别的都作废」：
        - ``play_action``：入队前调用（替换/停止后重新开播）；
        - ``pump_once`` 的表演键路径：动作本体解析出来后调用（按键没有入队入口）。
        """
        self._play_seq += 1
        self._active_play_seq = self._play_seq
        return self._play_seq

    def _end_play(self, seq: int | None) -> None:
        """结束一次播放：**只清自己的令牌**。

        关键不变量：A 播完时若 B 已激活，``seq``（A）≠ ``_active_play_seq``（B），
        于是什么也不清 —— B 的忙标志不会被 A 的收尾动作误清。这正是缺陷 1 的修复点。
        调用方必须持 ``self._lock``。
        """
        if seq is not None and seq == self._active_play_seq:
            self._active_play_seq = None

    def _invalidate_plays(self) -> None:
        """让所有在途播放失效（``stop_action`` 用）；调用方必须持 ``self._lock``。"""
        self._play_seq += 1
        self._active_play_seq = None

    def _is_current_play(self, command: Command) -> bool:
        """该命令是否就是当前唯一合法的那次播放（须在 ``self._lock`` 内调用）。"""
        seq = (command.payload or {}).get("play_seq")
        return seq is not None and seq == self._active_play_seq

    def _motion_cancelled(self, command: Command) -> bool:
        with self._lock:
            return (command.generation != self.state_machine.generation
                    or not self._is_current_play(command)
                    or self.state_machine.state is State.FAULT_LOCKED)

    # ---------- 队列消费 ----------
    def pump_once(self) -> bool:
        """消费一个工作命令；返回是否消费了命令（供 worker 与测试单步）。"""
        command: Command | None = self.work.get(timeout=0)
        if command is None:
            return False
        if command.kind is CommandKind.PLAY_ACTION:
            if not command.payload.get("action"):
                # 物理表演键路径：core 只打了 ``perform_intent=greet`` 语义标记，
                # 动作本体在这里（持有 roles 的一侧）解析。
                #
                # 解析不出来（角色没装 / 动作包缺 greet / 文件非法）时**保持原语义**：
                # 按 ``_finish_without_motion`` 收尾回 IDLE，不动硬件。
                #
                # **必须兜住一切异常**（P0）：本函数在 ``run_forever`` 的循环里被直接
                # 调用，异常穿出去只会让诊断计数加一然后继续下一轮 —— 但「这一轮」的
                # PERFORM_DONE 永远不会发，状态机卡在 PERFORMING 且没有任何超时兜底
                # （只有再按一次表演键才能回到 IDLE）。
                # 内容/装配问题都该降级成「本轮无动作」，而不是废掉一次按键。
                # 不要写成 ``except Exception: pass``：必须记 error 日志，否则真 bug
                # 会被永久掩盖（这是上一批假绿整改的教训）。
                try:
                    resolved = self._resolve_greet(command)
                except Exception as exc:  # noqa: BLE001  线程存活与状态收敛优先
                    if self.log is not None:
                        self.log.error(
                            "[motion worker] 表演键动作解析异常，本轮按无动作收尾：%s",
                            exc, exc_info=True)
                    self._finish_without_motion(None, command.generation)
                    return True
                if resolved is None:
                    # 解析失败也走「无动作收尾」，但收尾函数内部会复核代数 ——
                    # 若这一下按键期间已被取消/换角色，连 PERFORM_DONE 都不能投。
                    self._run_play(command)
                    return True
                command = resolved
                # 按键路径没有 ``ports.play_action`` 那种「入队即取令牌」的入口，
                # 必须在守卫之前显式激活令牌；否则守卫会把它当陈旧命令丢弃 →
                # PERFORM_DONE 永不产生 → 永久卡 PERFORMING（第 2 组修过的坑）。
                with self._lock:
                    if command.generation != self.state_machine.generation:
                        return True  # 解析期间被取消/换角色，本条作废
                    seq = self._begin_play()
                    command = replace(command, payload={**dict(command.payload or {}),
                                                        "play_seq": seq})
                    self.action_progress = {
                        "action_id": GREET_ACTION_ID, "seq": 0, "progress_ms": 0,
                    }
            with self._lock:
                if not self._is_current_play(command):
                    return True  # 已被 stop/替换：丢弃陈旧运动命令，不移动硬件
            self._run_play(command)
        elif command.kind not in MOTION_SILENT_KINDS:
            # 静默丢弃会让排障无从下手（P0-1）：不认识的命令必须能被看见。
            if self.log is not None:
                self.log.warning("[motion worker] 收到未处理命令 %s，已丢弃",
                                 command.kind.value)
        return True

    def _resolve_greet(self, command: Command) -> Command | None:
        """把带 ``perform_intent`` 标记的 PLAY_ACTION 补全成可直接播放的载荷。

        非打招呼意图（当前无其它意图）或解析失败，一律返回 ``None``，由调用方
        沿用「空载荷 → 不动硬件直接收尾」的既有语义。
        """
        if command.payload.get("perform_intent") != GREET_INTENT:
            return None
        payload = self.resolve_greet_action()   # 宿主（AppContext）提供
        if payload is None:
            return None
        return replace(command, payload={**dict(command.payload or {}), **payload})

    def _finish_without_motion(self, seq: int | None = None,
                              generation: int | None = None) -> None:
        """无动作载荷的 PERFORM_PRESS（如按键表演未选动作）：不移动硬件，直接收尾回 IDLE。

        ``generation`` 非 None 时先复核：若这一下按键期间已发生取消/换角色，则**不投**
        ``PERFORM_DONE``。否则一条迟到 5ms 的收尾事件会把**新会话**正在表演的动作
        直接拽回 IDLE（``PERFORMING --PERFORM_DONE--> IDLE`` 是合法迁移）。
        """
        with self._lock:
            if generation is not None and generation != self.state_machine.generation:
                self._end_play(seq)
                return
            still_current = seq is not None and seq == self._active_play_seq
            self._end_play(seq)
            if seq is None or still_current:
                self.action_progress = None
            self.bus.publish(Event(EventKind.PERFORM_DONE,
                                   generation=self.state_machine.generation))

    def _set_fault(self, exc: Exception) -> None:
        self.fault = {"code": fault_code(exc), "detail": str(exc) or type(exc).__name__}
        if self.log is not None:
            self.log.error("motion fault: %s (%s)", self.fault["code"], self.fault["detail"])

    # ---------- 排空 ----------
    def dispatch_bus(self, limit: int = DRAIN_LIMIT) -> int:
        """只推进事件总线（状态机迁移），不触碰工作队列；返回处理步数。"""
        steps = 0
        with self._lock:
            while steps < limit and self.bus.qsize() > 0:
                self.engine.step(timeout=0)
                steps += 1
        return steps

    def drain(self, limit: int = DRAIN_LIMIT) -> int:
        """排空待处理工作。worker 运行时**只**推进总线（工作队列归 worker 独占）。"""
        if self.worker_running:
            return self.dispatch_bus(limit)
        steps = 0
        with self._lock:
            while steps < limit:
                if self.work.qsize() > 0:
                    self.pump_once()
                elif self.bus.qsize() > 0:
                    self.engine.step(timeout=0)
                else:
                    break
                steps += 1
        return steps

    def run_forever(self, stop_event=None) -> None:
        """worker 循环：执行运动命令并推进状态机；空闲时按 5ms 让出，避免空转。

        **禁止静默死亡**（P0-2）：任何未被下层捕获的异常都在此记日志并降级继续，
        同时刷新心跳（``worker_heartbeat_ms``）供 ``/status`` 的存活探针读取。
        """
        try:
            self._run_motion_loop(stop_event)
        finally:
            self.db.close()  # 保持 check_same_thread=True；在所属线程释放连接。

    def _run_motion_loop(self, stop_event) -> None:
        while not self._shutdown.is_set() and not (
            stop_event is not None and stop_event.is_set()
        ):
            self.worker_heartbeat_ms = self.clock.now_ms()
            try:
                if self.work.qsize() > 0:
                    self.pump_once()
                else:
                    self.dispatch_bus()
            except Exception as exc:  # noqa: BLE001  兜底：线程存活优先于单次失败
                #裸自增**实测不会丢计数**，故意不加锁（理由与证据见 ports.py 的
                # worker_faults 字段注释）。不要"顺手"改成读-改-写分步或加锁 ——
                # 那会让真正的编程错误更难查，而诊断计数器精度不值得这个代价。
                self.worker_faults += 1
                if self.log is not None:
                    self.log.error("worker 循环异常（已捕获并降级继续）：%s", exc, exc_info=True)
                self.clock.sleep(WORKER_IDLE_S * 4)
                continue
            self.clock.sleep(WORKER_IDLE_S)
