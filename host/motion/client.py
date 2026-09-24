"""``MotionClient``：STATUS → ARM → FRAME → SETTLED → STOP 状态机。

保留冻结基线 ``software/motion.py::play`` 的**顺序与阈值**（架构 §5 兼容契约）：

- 开串口后先等 ``boot_wait`` 秒（真机 2s，等固件就绪/角色稳定）再 ``reset_input_buffer``；
- ``STOP`` + ``STATUS`` 前置检查 ``state[1:] == [1, 1, role]``；
- ``ARM`` 确认窗口 1s；
- 持续发 ``FRAME``（无 ACK），末帧后**保持 >=150ms** 再 ``STOP``；
- 状态回报超时 0.8s；收尾总超时 ``end_ms/1000 + 2s``；
- 收到 ``ERR``/``FAULT`` 立即中止；``finally`` **必定**发出 ``STOP``；
- **绝不自动重新解锁**（不重发 ARM）。

差异只有两点（均已声明的改进，不改变对外行为）：依赖注入 ``Clock`` + ``MotionLink``；
动作校验改由 ``host/motion/validation.py`` 单一真源提供。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from host.core.errors import InterlockError, MotionTimeout, ProtocolError
from host.motion.timeline import Timeline
from host.motion.validation import validate

BOOT_WAIT_S = 2.0
STATUS_TIMEOUT_S = 2.0
ARM_TIMEOUT_S = 1.0
STATE_REPORT_TIMEOUT_S = 0.8
HOLD_FINAL_S = 0.15
TAIL_TIMEOUT_S = 2.0
POLL_S = 0.02


@dataclass(frozen=True, slots=True)
class PlayResult:
    role_id: int
    frames_sent: int
    final_seq: int
    hold_ms: int
    end_ms: int


class MotionClient:
    """把动作时间线播到 MCU，并在任何异常路径上安全停机。"""

    def __init__(self, link, clock, *, boot_wait_s: float = BOOT_WAIT_S, logger=None) -> None:
        self.link = link
        self.clock = clock
        self.boot_wait_s = boot_wait_s
        self.logger = logger

    def play(self, action: Mapping[str, Any], *, role_id: int | None = None) -> PlayResult:
        validate(action)
        expected_role = int(role_id if role_id is not None else action["role_id"])
        link, clock = self.link, self.clock
        try:
            link.open()
            clock.sleep(self.boot_wait_s)
            link.reset_input_buffer()
            link.write_line("STOP")
            link.write_line("STATUS")
            state = self._await_state()
            # STATE 的内容为 解锁状态 到位状态 急停健康状态 角色编号。
            if not state or state[1:] != [1, 1, expected_role]:
                raise InterlockError("到位、急停或角色编号未通过检查")
            link.write_line(f"ARM {expected_role}")
            self._await_arm(expected_role)
            return self._stream(action, expected_role)
        finally:
            self._safe_stop_and_close(link)

    def _safe_stop_and_close(self, link) -> None:
        """收尾必须**无条件**执行：播放中被拔线时写 STOP 会抛 OSError，但 close() 仍必须跑。

        ``link.open()`` 失败时 ``write_line`` 会抛 ``RuntimeError``（串口未打开），同样必须
        被吞下，好让异常沿 ``play()`` 原样上抛（由 ``_run_play`` 归类为故障）。
        """
        try:
            link.write_line("STOP")
        except Exception as exc:  # noqa: BLE001  收尾阶段不允许再抛，否则掩盖真实故障
            if self.logger is not None:
                self.logger.warning("收尾发 STOP 失败（继续关闭串口）：%s", exc)
        try:
            link.close()
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.warning("关闭串口失败：%s", exc)

    # ---------- 前置阶段 ----------
    def _await_state(self) -> list[int] | None:
        deadline = self.clock.now_ms() + int(STATUS_TIMEOUT_S * 1000)
        while self.clock.now_ms() < deadline:
            for line in self.link.read_lines():
                if line.startswith("STATE "):
                    return [int(x) for x in line.split()[1:]]
            self.clock.sleep(POLL_S)
        return None

    def _await_arm(self, expected_role: int) -> None:
        deadline = self.clock.now_ms() + int(ARM_TIMEOUT_S * 1000)
        while self.clock.now_ms() < deadline:
            for line in self.link.read_lines():
                if line == f"OK ARM {expected_role}":
                    return
                if line.startswith("ERR") or line.startswith("FAULT"):
                    raise ProtocolError(line)
            self.clock.sleep(POLL_S)
        raise MotionTimeout("控制器没有确认解锁")

    # ---------- 播放主循环 ----------
    def _stream(self, action: Mapping[str, Any], expected_role: int) -> PlayResult:
        clock = self.clock
        timeline = Timeline(action, clock)
        timeline.begin()
        last_state_ms = clock.now_ms()
        seq = 0
        final_seq: int | None = None
        settled_at_ms: int | None = None
        try:
            while True:
                values = timeline.frame()
                if timeline.at_end() and final_seq is None:
                    final_seq = seq
                self.link.write_line(f"FRAME {seq} " + " ".join(map(str, values)))
                seq += 1

                for line in self.link.read_lines():
                    if line.startswith("ERR") or line.startswith("FAULT"):
                        raise ProtocolError(line)
                    if (
                        line.startswith("SETTLED ")
                        and final_seq is not None
                        and int(line.split()[1]) >= final_seq
                        and settled_at_ms is None
                    ):
                        settled_at_ms = clock.now_ms()
                    if line.startswith("STATE "):
                        current = [int(x) for x in line.split()[1:]]
                        if current != [1, 1, 1, expected_role]:
                            raise InterlockError("运动期间安全状态或角色变化")
                        last_state_ms = clock.now_ms()

                now = clock.now_ms()
                if now - last_state_ms > STATE_REPORT_TIMEOUT_S * 1000:
                    raise MotionTimeout("控制器状态回报超时")
                if settled_at_ms is not None and now - settled_at_ms >= HOLD_FINAL_S * 1000:
                    break  # 末帧目标已确认且保持 >=150ms
                if timeline.overshoot_ms() > TAIL_TIMEOUT_S * 1000:
                    raise MotionTimeout("控制器未确认末帧目标输出完成")
                clock.sleep(POLL_S)
        finally:
            self.link.write_line("STOP")

        end_now = clock.now_ms()
        return PlayResult(
            role_id=expected_role,
            frames_sent=seq,
            final_seq=final_seq if final_seq is not None else seq - 1,
            hold_ms=int(end_now - settled_at_ms) if settled_at_ms is not None else 0,
            end_ms=timeline.end_ms,
        )
