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

    def inject_dock(self) -> dict:
        """模拟公仔重新插到位 / 急停复位。

        与 :meth:`inject_undock` 成对存在，**不是**解除故障的手段——本方法只
        恢复硬件前置条件，仍锁定在 ``FAULT_LOCKED``，由用户另行确认
        :meth:`clear_fault`。少了它，``clear_fault`` 的硬件校验会让「拔出后
        忘记复位」变成无法自救的死锁。
        """
        if self.sim is not None:
            self.sim.dock.engaged = True
            self.sim.estop.pressed = False
        return self.status_snapshot()

    def clear_fault(self) -> dict:
        """人工确认后解除故障锁定（唯一收敛路径，绝不自动恢复）。

        **必须先校验硬件前置条件**。固件语义（``sim/controller.py:loop_once``）：
        ``armed and (not docked or not healthy)`` 会立即 ``stop_motion()`` 并回
        ``FAULT INTERLOCK_OR_TIMEOUT``。此前只看状态机是否在 FAULT_LOCKED，
        于是「公仔仍拔出」也能确认解除、回 IDLE、UI 显示已恢复，紧接着固件
        再次跳闸——用户看到的是「我解除了但马上又坏了」，属假可用态。
        硬件未恢复时必须**拒绝解除**并说明原因（前端要显示，不是静默失败）。
        """
        with self._lock:
            if self.state_machine.state is not State.FAULT_LOCKED:
                return {"cleared": False, "state": self.state_machine.state.value}
            blockers = self._fault_blockers()
            if blockers:
                return {
                    "cleared": False,
                    "state": self.state_machine.state.value,
                    "reason": f"故障前置条件未恢复：{'、'.join(blockers)}",
                    "blockers": blockers,
                }
            self.fault = None
            self._deliver(self.engine.apply(Trigger.FAULT_CLEAR, {"confirmed": True}))
            if self.state_machine.state is State.SELF_TEST:
                self._deliver(self.engine.apply(
                    Trigger.BOOT_OK, {"mcu_ready": True, "role_stable": True}
                ))
            self.drain()
            return {"cleared": True, "state": self.state_machine.state.value}

    def _fault_blockers(self) -> list[str]:
        """当前阻止解除的前置条件（空列表 = 可解除）。

        以 ``_mcu_snapshot`` 为单一数据源：模拟档位读进程内 MCU 模拟器，
        真机档位读 ``mcu_docked`` / ``mcu_healthy``，两条路径同源。
        """
        mcu = self._mcu_snapshot()
        blockers: list[str] = []
        if not mcu["docked"]:
            blockers.append("公仔未插到位")
        if not mcu["healthy"]:
            blockers.append("急停未复位")
        return blockers
