"""带播放归属和有效期的状态快照；不把发出的目标当作实测位置。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host.core.events import Command
from host.core.state_machine import State
from host.motion.client import MotionProgress

WORKER_LIVENESS_MS = 5000
TARGET_TTL_MS = 1000


@dataclass(frozen=True, slots=True)
class TargetSample:
    owner: int
    generation: int
    config_version: str
    action_id: str
    captured_ms: int
    progress: MotionProgress


class StatusMixin:
    def _record_motion_progress(self, command: Command, version: str,
                                progress: MotionProgress) -> None:
        with self._lock:
            config = self.roles.active()
            if (not self._is_current_play(command)
                    or command.generation != self.state_machine.generation
                    or self.state_machine.state is not State.PERFORMING
                    or self._shutdown.is_set() or config is None
                    or progress.role_id != config.role_id or version != config.config_version):
                return
            self._target_sample = TargetSample(
                command.payload["play_seq"], command.generation, version,
                str(command.payload.get("action_id", "")), progress.sent_at_ms, progress)
            self.action_progress = {"action_id": self._target_sample.action_id,
                                    "seq": progress.seq, "progress_ms": progress.progress_ms}

    def _target_snapshot(self, config) -> dict[str, Any] | None:
        sample = self._target_sample
        if (sample is None or config is None or self._shutdown.is_set()
                or self.state_machine.state is not State.PERFORMING or self.fault is not None
                or sample.owner != self._active_play_seq
                or sample.generation != self.state_machine.generation
                or sample.progress.role_id != config.role_id
                or sample.config_version != config.config_version):
            return None
        age = self.clock.now_ms() - sample.captured_ms
        if not 0 <= age < TARGET_TTL_MS:
            return None
        progress = sample.progress
        return {"source": "sent_frame", "role_id": progress.role_id,
                "config_version": sample.config_version, "action_id": sample.action_id,
                "seq": progress.seq, "progress_ms": progress.progress_ms,
                "target_us": list(progress.target_us), "valid_for_ms": TARGET_TTL_MS - age}

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            mcu = self._mcu_snapshot()
            fault = dict(self.fault) if self.fault is not None else None
            if fault is None and self.state_machine.state is State.FAULT_LOCKED:
                fault = {"code": "SAFETY_TRIP", "detail": "安全联锁已锁定，需人工确认"}
            config = self.roles.active()
            limits = None if config is None else {
                "role_id": config.role_id, "config_version": config.config_version,
                "min_us": config.min_us, "max_us": config.max_us,
                "neutral_us": config.neutral_us}
            return {
                "state": self.state_machine.state.value,
                "mcu_armed": mcu["armed"], "docked": mcu["docked"],
                "estop_healthy": mcu["healthy"], "role_id": self.roles.active_role_id or 0,
                "session_generation": self.state_machine.generation,
                "network_online": bool(self.network_online), "fault": fault,
                "action": dict(self.action_progress) if self.action_progress is not None else None,
                "target": self._target_snapshot(config), "axis_limits": limits,
                "worker_alive": self.worker_alive(), "worker_faults": int(self.worker_faults),
            }

    def _mcu_snapshot(self) -> dict[str, Any]:
        if self.sim is not None:
            return {
                "armed": bool(self.sim.armed),
                "docked": self.sim.docked(),
                "healthy": self.sim.healthy(),
            }
        return {"armed": self.mcu_armed, "docked": self.mcu_docked, "healthy": self.mcu_healthy}

    def worker_alive(self) -> bool:
        """存活探针：领域线程（net/audio/tick）**与 motion worker** 逐个健在。

        为什么不能只看心跳：``worker_heartbeat_ms`` 只由 motion worker 写
        （``execution.run_forever``），三个领域线程全死时心跳依然新鲜，
        ``/status`` 会返回假的 ``worker_alive: true`` —— 而领域线程一死，
        按对话键就永久停在 THINKING（P0-1 症状复发）。

        **为什么心跳不能作为判活依据（曾踩的坑）**：心跳写在 ``run_forever``
        的 ``while`` 循环**开头**，紧接着 ``pump_once()`` -> ``play_action()``
        **同步阻塞整个动作时长**（``client.py`` 轮询到末帧保持 150ms）。
        于是播放期间心跳全程不更新，超过 ``WORKER_LIVENESS_MS``（5s）后
        ``wave.json``（4000ms 时间轴）这类动作必然让探针在**线程全健在**时
        返回 False —— 谎报。且 ``TAIL_TIMEOUT_S`` 只管末帧之后的超时，
        **不限制时间轴本身**，故阻塞时长无固定上界，提高阈值是治标不治本。

        因此判活一律以 ``is_alive()`` 为准：motion 线程由 ``main.py`` 启动后
        登记到 ``_motion_thread``，与领域线程同源检查。
        心跳仅在**线程尚未登记**时作为兜底（保留第 2 组"启动窗口不误报"的成果）。
        """
        threads = list(getattr(self, "_domain_threads", []) or [])
        if not threads:
            return False  # 从未启动：不得报活
        if not all(getattr(t, "is_alive", lambda: False)() for t in threads):
            return False
        motion = getattr(self, "_motion_thread", None)
        if motion is not None:
            # 已登记：线程是否健在就是唯一依据，无论心跳多旧（可能正同步播放中）
            return bool(getattr(motion, "is_alive", lambda: False)())
        heartbeat = self.worker_heartbeat_ms
        if heartbeat is not None:
            return (self.clock.now_ms() - heartbeat) <= WORKER_LIVENESS_MS
        # 心跳尚无（motion worker 未启动或还没跑一轮）：领域线程健在即视为可用。
        return True
