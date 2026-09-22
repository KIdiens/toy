"""执行循环：**工作队列（运动）** 与 **事件总线（状态机）** 的消费与锁纪律。

线程模型（architecture §3 的落地）：

- HTTP 线程只做「校验 → 入队 / 投事件」，**不阻塞在任何 I/O 上**；
- ``run_forever`` 由 ``main`` 在**唯一** worker 线程里跑：先执行运动命令，再推进状态机；
- ``_run_play`` 里的 ``motion.play`` 长耗时调用**不持锁**，使急停 / 拔出注入即时生效（AC-22）；
- ``self._lock``（RLock）保证「同一时刻只有一个线程在改状态机」，即使 worker 与 HTTP 并存
  也不会破坏核心的单线程不变量。
"""
from __future__ import annotations

from host.core.errors import (
    ClarifyError,
    ConflictError,
    HostError,
    InterlockError,
    MotionTimeout,
    ProtocolError,
    ProviderError,
    StorageError,
)
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import Trigger

DRAIN_LIMIT = 2000
WORKER_IDLE_S = 0.005


class ExecutionMixin:
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
        """把核心命令投进工作队列（调用方通常已持锁）。"""
        for command in commands:
            self.work.put(command)

    # ---------- 队列消费 ----------
    def pump_once(self) -> bool:
        """消费一个工作命令；返回是否消费了命令（供 worker 与测试单步）。"""
        command: Command | None = self.work.get(timeout=0)
        if command is None:
            return False
        if command.kind is CommandKind.PLAY_ACTION:
            with self._lock:
                if not self.motion_busy:
                    return True  # 已被 stop/替换：丢弃陈旧运动命令，不移动硬件
            self._run_play(command)
        return True

    def _run_play(self, command: Command) -> None:
        """在工作线程执行一个动作；长调用不持锁，结果回写时再取锁。"""
        payload = command.payload
        action = payload.get("action")
        if not action:
            self._finish_without_motion()
            return
        try:
            if self.motion is None:
                raise ConflictError("未配置运动链路")
            result = self.motion.play(action, role_id=int(payload["role_id"]))
        except (HostError, ValueError) as exc:
            with self._lock:
                self.motion_busy = False
                self._set_fault(exc)
                self.bus.publish(Event(EventKind.SAFETY_TRIP, payload={"fault": self.fault},
                                       generation=self.state_machine.generation))
            return
        with self._lock:
            if command.generation != self.state_machine.generation:
                self.motion_busy = False
                return  # 期间被取消/换角色：丢弃本轮结果，不回写进度也不发完成事件
            self.last_result = result
            self.fault = None
            self.motion_busy = False
            self.action_progress = {
                "action_id": str(payload.get("action_id", "")),
                "seq": result.frames_sent,
                "progress_ms": result.end_ms,
            }
            self.bus.publish(Event(EventKind.PERFORM_DONE,
                                   generation=self.state_machine.generation))

    def _finish_without_motion(self) -> None:
        """无动作载荷的 PERFORM_PRESS（如按键表演未选动作）：不移动硬件，直接收尾回 IDLE。"""
        with self._lock:
            self.motion_busy = False
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
        """worker 循环：执行运动命令并推进状态机；空闲时按 5ms 让出，避免空转。"""
        while not (stop_event is not None and stop_event.is_set()):
            if self.work.qsize() > 0:
                self.pump_once()
            else:
                self.dispatch_bus()
            self.clock.sleep(WORKER_IDLE_S)


def fault_code(exc: Exception) -> str:
    """把受控异常映射为契约的故障码（enum: INTERLOCK/TIMEOUT/PWM_INIT/SAFETY_TRIP/...）。"""
    if isinstance(exc, MotionTimeout):
        return "TIMEOUT"
    if isinstance(exc, InterlockError):
        return "INTERLOCK"
    if isinstance(exc, ProtocolError):
        return "TIMEOUT" if "TIMEOUT" in str(exc) else "INTERLOCK"
    if isinstance(exc, ProviderError):
        return "PROVIDER"
    if isinstance(exc, StorageError):
        return "STORAGE"
    if isinstance(exc, ClarifyError):
        return "INTERLOCK"
    return "SAFETY_TRIP"
