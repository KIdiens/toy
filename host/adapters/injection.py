"""事件注入：按键 / 安全跳闸 / 拔出到位信号 / 解除故障。

用于 ``dev_sim`` 路由与测试注入物理事件。**只投事件、不直接改状态**，
让状态机按表决定迁移；``drain`` 在 worker 运行时只推进总线，故注入即时生效。
"""
from __future__ import annotations

from host.core.events import Event, EventKind
from host.core.state_machine import State, Trigger


class InjectionMixin:
    """依赖宿主提供 ``bus`` / ``engine`` / ``state_machine`` / ``work`` / ``sim`` / ``_lock``。"""

    def press_button(self, number: int) -> dict:
        """注入一次按键；按键行为由状态机按键表决定。"""
        with self._lock:
            self.bus.publish(Event(EventKind.BUTTON, payload={"n": int(number)},
                                   generation=self.state_machine.generation))
            self.drain()
            return {"state": self.state_machine.state.value}

    def trip_safety(self, code: str, detail: str) -> dict:
        """注入 SAFETY_TRIP（唯一可抢占任意态的迁移，已锁定态除外）。"""
        with self._lock:
            self.fault = {"code": code, "detail": detail}
            self.bus.publish(Event(EventKind.SAFETY_TRIP, payload={"fault": self.fault},
                                   generation=self.state_machine.generation))
            self.drain()
            return self.status_snapshot()

    def inject_undock(self) -> dict:
        """模拟公仔被拔出：位信号丢失 → 联锁跳闸（AC-03/AC-24）。

        只做一次属性写入（原子）后交状态机处理；**不**在此调用 ``sim.poll()``，
        避免与正在执行的运动循环并发跑 MCU 主循环造成竞态。
        """
        if self.sim is not None:
            self.sim.dock.engaged = False
        return self.trip_safety("INTERLOCK", "公仔被拔出（到位信号丢失）")

    def clear_fault(self) -> dict:
        """人工确认后解除故障锁定（唯一收敛路径，绝不自动恢复）。"""
        with self._lock:
            if self.state_machine.state is not State.FAULT_LOCKED:
                return {"cleared": False, "state": self.state_machine.state.value}
            self.fault = None
            self._deliver(self.engine.apply(Trigger.FAULT_CLEAR, {"confirmed": True}))
            if self.state_machine.state is State.SELF_TEST:
                self._deliver(self.engine.apply(
                    Trigger.BOOT_OK, {"mcu_ready": True, "role_stable": True}
                ))
            self.drain()
            return {"cleared": True, "state": self.state_machine.state.value}
