"""固件等价状态机：逐字复刻 ``firmware/motion_controller/motion_controller.ino``。

函数与状态变量**故意与 .ino 同名同义**，便于逐行对照审查。保真矩阵见
``docs/architecture/20-mcu-simulator.md`` §3。

关键复刻点（每一条都有回归测试守护）：

1. ``previousSeq`` 在 ``stop_motion()`` 中**不重置** —— 这是有意的重放防护
   （架构 §10.2 D3），不是缺陷。
2. ``ARM`` 按 ``sscanf("ARM %d %c") == 1`` 解析：任何尾随字符（含空格）都会使
   赋值字段数变成 2，于是落入 ``ERR COMMAND``（架构 §10.2 D6）。
3. 联锁求值**顺序**与固件一致（影响报哪个错）。
4. 联锁判定在 ``ALREADY_ARMED`` **之前**：已 armed 且急停按下时报 INTERLOCK。
5. 只有**有效 FRAME** 刷新 300ms 看门狗；``STATUS`` 不能维持运动。
6. 每循环最多从串口消费 64 字节，通信洪泛不阻塞安全检查。
"""
from __future__ import annotations

from sim.devices import Buttons, Dock, Estop, RoleResistor
from sim.wire import BYTES_PER_CYCLE, Wire, parse_arm, parse_frame

# 固件常量（逐字来自 motion_controller.ino）。
LOW_LIMIT_US = 1400
HIGH_LIMIT_US = 1600
PWM_PERIOD_MS = 20
PWM_STEP_US = 6
WATCHDOG_MS = 300
STABLE_MS = 200
REPORT_MS = 200
PWM_BITS = 16383
PWM_DENOM = 20000
PWM_NUM_OFFSET = 10000
MAX_CATCHUP_MS = 5000


class McController:
    """ESP32 四轴控制器（含联锁、看门狗、PWM 步进）的等价模型。"""

    def __init__(
        self,
        clock=None,
        *,
        pwm_ready: bool = True,
        dock: Dock | None = None,
        estop: Estop | None = None,
        role: RoleResistor | None = None,
        buttons: Buttons | None = None,
        logger=None,
    ) -> None:
        if clock is None:
            from sim.clock import SimClock

            clock = SimClock()
        self.clock = clock
        self.pwm_ready = pwm_ready
        self.dock = dock if dock is not None else Dock()
        self.estop = estop if estop is not None else Estop()
        self.role_res = role if role is not None else RoleResistor()
        self.buttons = buttons if buttons is not None else Buttons()
        self.logger = logger

        # —— 固件全局变量，同名同义 ——
        self.armed = False
        self.have_seq = False
        self.active_role = 0
        self.stable_role = 0
        self.candidate_role = 0
        self.current_us = [1500, 1500, 1500, 1500]
        self.target_us = [1500, 1500, 1500, 1500]
        self.last_frame = 0
        self.last_tick = 0
        self.last_report = 0
        self.candidate_since = 0
        self.previous_seq = 0
        self.duty = [0, 0, 0, 0]  # 记录 duty 供断言（对应 ledcWrite）

        self._out: list[str] = []
        self._rx = bytearray()
        self._wire = Wire()
        self._loop_ms = 0
        self._booted = False

    # ---------- 设备查询（对应固件的三个读脚函数） ----------
    def docked(self) -> bool:
        return self.dock.docked()

    def healthy(self) -> bool:
        return self.estop.healthy()

    def read_role(self) -> int:
        return self.role_res.read()

    # ---------- 上电（setup） ----------
    def power_on(self) -> None:
        self._ensure_boot()

    def _ensure_boot(self) -> None:
        if self._booted:
            return
        self._booted = True
        self._emit("READY 1" if self.pwm_ready else "FAULT PWM_INIT")

    # ---------- stopMotion / report / command ----------
    def stop_motion(self) -> None:
        self.armed = False
        self.have_seq = False
        for i in range(4):
            self.duty[i] = 0
        # previousSeq **刻意不重置**：有意的重放防护（架构 §10.2 D3）。

    def report(self) -> None:
        self._emit(
            f"STATE {int(self.armed)} {int(self.docked())} "
            f"{int(self.healthy())} {self.stable_role}"
        )
        settled = self.armed and self.have_seq
        if settled:
            for i in range(4):
                if self.current_us[i] != self.target_us[i]:
                    settled = False
                    break
        if settled:
            self._emit(f"SETTLED {self.previous_seq}")

    def command(self, line: str) -> None:
        if line == "STOP":
            self.stop_motion()
            self._emit("OK STOP")
            return
        if line == "STATUS":
            self.report()
            return

        expected_role = parse_arm(line)
        if expected_role is not None:
            now = self._loop_ms
            if (
                not self.pwm_ready
                or not self.docked()
                or not self.healthy()
                or not self.stable_role
                or self.read_role() != self.stable_role
                or expected_role != self.stable_role
                or self.candidate_role != self.stable_role
                or now - self.candidate_since < STABLE_MS
            ):
                self._emit("ERR INTERLOCK")
                return
            if self.armed:
                self._emit("ERR ALREADY_ARMED")
                return
            self.active_role = self.stable_role
            for i in range(4):
                self.current_us[i] = self.target_us[i] = 1500
            self.have_seq = False
            self.last_frame = now
            self.armed = True
            self._emit(f"OK ARM {self.active_role}")
            return

        parsed = parse_frame(line)
        if parsed is not None:
            seq, values = parsed
            valid = self.armed and (not self.have_seq or seq > self.previous_seq)
            for value in values:
                valid = valid and LOW_LIMIT_US <= value <= HIGH_LIMIT_US
            if not valid:
                self.stop_motion()
                self._emit("ERR FRAME")
                return
            self.target_us = list(values)
            self.previous_seq = seq
            self.have_seq = True
            self.last_frame = self._loop_ms
            return  # 固件对有效 FRAME 无 ACK

        self.stop_motion()
        self._emit("ERR COMMAND")

    # ---------- 主循环 ----------
    def loop_once(self, now: int) -> None:
        self._ensure_boot()
        self._loop_ms = now
        role = self.read_role() if self.docked() else 0
        if role != self.candidate_role:
            self.candidate_role = role
            self.candidate_since = now
            self.stable_role = 0
        if not self.docked():
            self.stable_role = 0
        if now - self.candidate_since >= STABLE_MS:
            self.stable_role = self.candidate_role
        if self.armed and (
            not self.docked()
            or not self.healthy()
            or role != self.active_role
            or now - self.last_frame > WATCHDOG_MS
        ):
            self.stop_motion()
            self._emit("FAULT INTERLOCK_OR_TIMEOUT")

        if self._rx:
            chunk = bytes(self._rx[:BYTES_PER_CYCLE])
            del self._rx[:BYTES_PER_CYCLE]
            for event in self._wire.feed(chunk):
                if event.overflow:
                    self.stop_motion()
                    self._emit("ERR LENGTH")
                else:
                    self.command(event.line)

        if now - self.last_tick >= PWM_PERIOD_MS:
            self.last_tick = now
            if self.armed:
                for i in range(4):
                    delta = self.target_us[i] - self.current_us[i]
                    step = max(-PWM_STEP_US, min(PWM_STEP_US, delta))
                    self.current_us[i] += step
                    self.duty[i] = (
                        self.current_us[i] * PWM_BITS + PWM_NUM_OFFSET
                    ) // PWM_DENOM

        for number in self.buttons.update(now):
            self._emit(f"BUTTON {number}")

        if now - self.last_report >= REPORT_MS:
            self.last_report = now
            self.report()

    # ---------- 时间推进 / 输入 / 输出 ----------
    def receive(self, data: bytes) -> None:
        self._rx.extend(data)

    def has_pending_input(self) -> bool:
        return bool(self._rx)

    def run_until(self, now_ms: int) -> None:
        """把主循环推进到 ``now_ms``（逐毫秒步进，保证看门狗/报告节奏正确）。"""
        self._ensure_boot()
        if now_ms <= self._loop_ms:
            if self._rx:
                self.loop_once(self._loop_ms)
            return
        if now_ms - self._loop_ms > MAX_CATCHUP_MS:
            self._loop_ms = now_ms - 1
        for tick in range(self._loop_ms + 1, now_ms + 1):
            self.loop_once(tick)
        self._loop_ms = now_ms

    def tick(self) -> None:
        self.run_until(self.clock.now_ms())

    def poll(self) -> None:
        """在**当前**时刻跑一次主循环而不推进时间。

        用于让「同一时刻注入的物理变化」（拔线/急停/换角色）立刻参与联锁求值，
        以及把刚写入的整行消费完（对应固件每圈 64 字节预算的连续多圈）。
        """
        self._ensure_boot()
        self.loop_once(self._loop_ms)

    def take_output(self) -> list[str]:
        """取出并清空待发整行（丢失发射时刻；SimLink 用）。"""
        self._ensure_boot()
        out, self._out = self._out, []
        return [line for _t, line in out]

    def take_events(self) -> list[tuple[int, str]]:
        """取出并清空 ``(发射时刻 ms, 行)``，供黄金时间轴比对。"""
        self._ensure_boot()
        out, self._out = self._out, []
        return out

    def clear_output(self) -> None:
        self._out.clear()

    def _emit(self, line: str) -> None:
        self._out.append((self._loop_ms, line))
        if self.logger is not None:
            self.logger.debug("mcu> %s", line)
