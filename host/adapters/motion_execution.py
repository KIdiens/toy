"""在途运动执行、取消与结果回投；队列调度继续由 ExecutionMixin 负责。"""
from __future__ import annotations

from host.core.errors import (
    ClarifyError,
    ConflictError,
    HostError,
    InterlockError,
    MotionCancelled,
    MotionTimeout,
    ProtocolError,
    ProviderError,
    StorageError,
)
from host.core.events import Command, Event, EventKind
from host.core.state_machine import State


class MotionExecutionMixin:
    """依赖宿主的播放令牌、锁、状态机、运动客户端及事件总线。"""

    def _run_play(self, command: Command) -> None:
        """在工作线程执行一个动作；长调用不持锁，结果回写时再取锁。"""
        payload = command.payload
        action = payload.get("action")
        seq = payload.get("play_seq")
        if not action:
            self._finish_without_motion(seq, command.generation)
            return
        try:
            if self.motion is None:
                raise ConflictError("未配置运动链路")
            role_id = int(payload["role_id"])
            # 第二道防线：入口（ports.play_action / resolve_greet_action）已按角色限幅
            # 校验过，这里在**真正下发前**再查一次。理由：那两处都是「读文件 → 校验 →
            # 入队」，而本函数消费的是队列里的载荷 —— 任何绕过入口直接塞进队列的路径
            # （未来的新端点、注入面、测试夹具）都会跳过入口校验。限幅是机械安全边界，
            # 必须在**紧贴硬件**的那一层再兜一次。
            #
            # 与入口的**失败语义刻意不同**：入口不合规走 409 / 静默降级（内容问题
            # 不该让设备锁死）；这里不合规意味着「载荷绕过了入口」，属装配/调用面的
            # 真实异常，交给既有 except 归成 SAFETY_TRIP + 故障锁定（需人工确认）。
            min_us, max_us = self.role_limits(role_id)
            progress_options = {}
            if getattr(self.motion, "supports_progress", False):
                version = self.roles.get(role_id).config_version
                progress_options["on_progress"] = lambda progress: self._record_motion_progress(
                    command, version, progress)
            result = self.motion.play(action, role_id=role_id,
                                      min_us=min_us, max_us=max_us,
                                      cancel=lambda: self._motion_cancelled(command),
                                      **progress_options)
        except MotionCancelled:
            with self._lock:
                still_current = self._is_current_play(command)
                self._end_play(seq)  # 只结束旧令牌，保留替换动作的忙状态与安全锁定。
                if still_current:
                    self.action_progress = None
            return
        except (HostError, ValueError, OSError) as exc:
            # OSError 覆盖 pyserial 的 SerialException（串口不存在/被占用/播放中被拔线），
            # 否则它会越过此处、打穿 worker 线程（P0-2）。
            with self._lock:
                self._end_play(seq)
                self.action_progress = None
                self._set_fault(exc)
                self.bus.publish(Event(EventKind.SAFETY_TRIP, payload={"fault": self.fault},
                                       generation=self.state_machine.generation))
            return
        with self._lock:
            if (command.generation != self.state_machine.generation
                    or not self._is_current_play(command)
                    or self.state_machine.state is State.FAULT_LOCKED):
                # 期间被取消/换角色：丢弃本轮结果，不回写进度也不发完成事件。
                # **只清自己的令牌**：若已被新的播放替换，B 的忙标志必须留着。
                still_current = self._is_current_play(command)
                self._end_play(seq)
                if still_current:
                    # 离开 PERFORMING 的异常路径必须清进度，否则 /status 会在 IDLE 里
                    # 一直显示「正在表演 0%」（P2-3）。
                    self.action_progress = None
                return
            self.last_result = result
            self.fault = None
            self._end_play(seq)
            self.action_progress = {
                "action_id": str(payload.get("action_id", "")),
                "seq": result.frames_sent,
                "progress_ms": result.end_ms,
            }
            self.bus.publish(Event(EventKind.PERFORM_DONE,
                                   generation=self.state_machine.generation))


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
