"""适配层端口：``AppContext`` —— 把 core/storage/ai/audio 句柄聚成**只服务上层**的门面。

- 这是 web ↔ core 的**唯一桥接点**：路由只调用本对象的方法，不触碰线程/仓储细节。
- 运动执行经 ``work`` 队列解耦：路由入队 + 置忙标志（同步返回），真实执行由 worker
  线程的 ``pump_once``/``run_forever`` 消费（见 ``execution``）；核心从不阻塞在 I/O 上。
- 状态快照把「核心状态 + MCU 信号 + 会话代数」合成契约形状（不返回任何密钥）。
- 线程纪律统一由 ``self._lock``（RLock）守护；执行在 ``ExecutionMixin``、
  注入在 ``InjectionMixin``、存储门面在 ``StorageFacade``。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host.adapters.execution import ExecutionMixin
from host.adapters.injection import InjectionMixin
from host.adapters.services import StorageFacade
from host.adapters.workers import WorkersMixin
from host.core.engine import Engine
from host.core.errors import ConflictError, StorageError
from host.core.events import CommandKind
from host.core.state_machine import SessionStateMachine, State, Trigger
from host.motion.client import MotionClient, PlayResult

# worker 心跳新鲜度阈值：超过即视为「线程已死」（/status 的 worker_alive 探针）。
WORKER_LIVENESS_MS = 5000


@dataclass
class AppContext(ExecutionMixin, InjectionMixin, WorkersMixin, StorageFacade):
    """运行期上下文（一个进程一份）。"""

    settings: Any
    clock: Any
    bus: Any
    work: Any
    engine: Engine
    state_machine: SessionStateMachine
    db: Any
    roles: Any
    catalog: Any
    router: Any
    player: Any
    recorder: Any
    scheduler: Any = None
    motion: MotionClient | None = None
    sim: Any = None
    simulate: bool = False
    network_online: bool = False
    mcu_docked: bool = True
    mcu_healthy: bool = True
    mcu_armed: bool = False
    motion_busy: bool = False
    worker_running: bool = False
    fault: dict[str, str] | None = None
    action_progress: dict[str, Any] | None = None
    last_result: PlayResult | None = None
    log: Any = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # ---- 领域工作线程（P0-1）：队列句柄、启停句柄、存活探针 ----
    net_queue: Any = None
    audio_queue: Any = None
    _router: Any = field(default=None, repr=False)
    _worker_stop: Any = field(default=None, repr=False)
    _domain_threads: list = field(default_factory=list, repr=False)
    worker_heartbeat_ms: int | None = None
    worker_faults: int = 0
    fired_reminders: list = field(default_factory=list)
    # ---- 对话闭环的录音/超时状态（net↔audio 协作）----
    _rec_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _rec_ready: threading.Event = field(default_factory=threading.Event, repr=False)
    _last_recording: Any = field(default=None, repr=False)
    _last_recording_gen: int = -1
    _rec_started_gen: int = -1
    _thinking_since_ms: int | None = None

    # ---------- 状态快照 ----------
    def _mcu_snapshot(self) -> dict[str, Any]:
        if self.sim is not None:
            return {
                "armed": bool(self.sim.armed),
                "docked": self.sim.docked(),
                "healthy": self.sim.healthy(),
            }
        return {"armed": self.mcu_armed, "docked": self.mcu_docked, "healthy": self.mcu_healthy}

    def status_snapshot(self) -> dict[str, Any]:
        mcu = self._mcu_snapshot()
        fault = self.fault
        if fault is None and self.state_machine.state is State.FAULT_LOCKED:
            fault = {"code": "SAFETY_TRIP", "detail": "安全联锁已锁定，需人工确认"}
        return {
            "state": self.state_machine.state.value,
            "mcu_armed": mcu["armed"],
            "docked": mcu["docked"],
            "estop_healthy": mcu["healthy"],
            "role_id": self.roles.active_role_id or 0,
            "session_generation": self.state_machine.generation,
            "network_online": bool(self.network_online),
            "fault": fault,
            "action": self.action_progress,
            "worker_alive": self.worker_alive(),
            "worker_faults": int(self.worker_faults),
        }

    def worker_alive(self) -> bool:
        """存活探针：worker 心跳是否新鲜（线程静默死亡时可被 /status 观察到，P0-2）。"""
        if self.worker_heartbeat_ms is None:
            return False
        return (self.clock.now_ms() - self.worker_heartbeat_ms) <= WORKER_LIVENESS_MS

    def role_summaries(self) -> list[dict[str, Any]]:
        return self.roles.summaries()

    def action_files(self) -> list[dict[str, Any]]:
        return self.roles.action_files()

    # ---------- 角色切换 ----------
    def switch_active_role(self, role_id: int) -> dict[str, Any]:
        with self._lock:
            if self.motion_busy or self.state_machine.state in (State.PERFORMING, State.RECORDING):
                raise ConflictError("当前状态不允许切换角色（正在运动或录音）")
            config = self.roles.set_active(int(role_id))  # 切换即 session_generation += 1
            return {"role_id": config.role_id, "config_version": config.config_version}

    # ---------- 动作校验 / 播放 / 停止 ----------
    def validate_action(self, action: dict[str, Any]) -> dict[str, Any]:
        from host.motion.validation import validate  # noqa: PLC0415  （延迟导入，避免循环）

        try:
            validate(action)
        except (ValueError, TypeError) as exc:
            return {"valid": False, "errors": [{"path": "action", "message": str(exc)}]}
        return {"valid": True, "errors": []}

    def play_action(self, *, role_id: int, action_id: str,
                    action_version: str | None = None,
                    replace_current: bool = False) -> dict[str, Any]:
        """校验 → 入队 → 置忙；真实运动由 worker 执行（同步返回，不阻塞 HTTP）。"""
        with self._lock:
            active = self.roles.active_role_id
            if active is None:
                raise ConflictError("未加载任何角色，无法播放")
            if int(role_id) != int(active):
                raise ConflictError(f"角色不匹配：请求 {role_id}，当前 {active}")
            if action_id not in self.roles.actions_for(active):
                raise ConflictError(f"非当前角色白名单动作: {action_id}")
            if self.state_machine.state is State.FAULT_LOCKED:
                raise ConflictError("处于故障锁定，需人工确认后才能运动")
            if self.motion_busy or self.state_machine.state is State.PERFORMING:
                if not replace_current:
                    raise ConflictError(
                        "已有动作在播放，默认拒绝叠加（可用 replace_current 显式替换）"
                    )
                self.stop_action()
            path = self.roles.action_path(active, action_id)
            if path is None:
                raise ConflictError(f"动作文件不存在: {action_id}")
            action = json.loads(Path(path).read_text(encoding="utf-8"))
            self.validate_action(action)
            self.roles.set_current_action(action_id)
            commands = self.engine.apply(
                Trigger.PERFORM_PRESS,
                {"action": action, "role_id": active, "action_id": action_id},
            )
            self._deliver(commands)
            self.motion_busy = any(c.kind is CommandKind.PLAY_ACTION for c in commands)
            if not self.motion_busy:
                raise ConflictError("当前状态不接受播放指令")
            self.action_progress = {"action_id": action_id, "seq": 0, "progress_ms": 0}
            return {"scheduled": True, "action_id": action_id, "seq": 0}

    def stop_action(self) -> dict[str, Any]:
        with self._lock:
            self.player.stop()
            self.motion_busy = False
            state = self.state_machine.state
            triggers: list[Trigger] = []
            if state is State.PERFORMING:
                triggers.append(Trigger.PERFORM_PRESS)
            elif state is State.SPEAKING:
                triggers.append(Trigger.PLAY_DONE)
            for trigger in triggers:
                self._deliver(self.engine.apply(trigger, {"stopped": True}))
            if not self.worker_running:
                # 单线程档位：清掉队列残留；worker 档位交由 pump_once 的陈旧命令丢弃逻辑处理
                while self.work.get(timeout=0) is not None:
                    pass
            self.action_progress = None
            return {"stopped": True}

    # ---------- 关停 ----------
    def close(self) -> None:
        self.stop_workers()  # 先停领域线程，再关总线/设备，避免线程在关停中投事件
        self.bus.shutdown()
        self.player.backend.close()
        if self._router is not None:
            self._router.close()
        try:
            self.db.close_all()
        except StorageError:  # 关停阶段的存储异常只记录，不掩盖
            if self.log is not None:
                self.log.warning("关闭数据库连接时出错", exc_info=True)
