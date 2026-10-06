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

from host.adapters.execution import GREET_ACTION_ID, ExecutionMixin
from host.adapters.injection import InjectionMixin
from host.adapters.services import StorageFacade
from host.adapters.workers import WorkersMixin
from host.core.engine import Engine
from host.core.errors import ConflictError, HostError, StorageError
from host.core.events import CommandKind
from host.core.state_machine import SessionStateMachine, State, Trigger
from host.motion.client import MotionClient, PlayResult
from host.roles.schema import FW_MAX_US, FW_MIN_US

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
    # motion worker（run_forever）由 main.py 单独起线程，必须在此登记引用 ——
    # 存活探针要靠它判活，见 worker_alive() 的说明。
    _motion_thread: Any = field(default=None, repr=False)
    worker_heartbeat_ms: int | None = None
    # 诊断计数器：由 execution.run_forever 的兜底 except 与 workers._on_worker_error
    # 各自 `+= 1`。**为什么明知非原子也不加锁**（实测结论，非推测）：
    #
    # 1. 影响面已核实：唯一消费方是下面 status_snapshot 里的 `int(self.worker_faults)`，
    #    只做 /status 诊断显示，全仓无任何逻辑分支读它的值（无阈值判断、无重试计数）。
    #    即丢计数最坏结果是「诊断数字偏小」，不可能导致状态机错乱。
    # 2. 竞态在 CPython 3.11+ 实测**不可触发**：字节码是
    #    LOAD_ATTR → BINARY_OP → STORE_ATTR，中间既无 CALL 也无向后跳转，
    #    而 CPython 只在这两处检查线程切换；STORE_ATTR 走的是 C 层 slot wrapper
    #    （MRO 里只有 object.__setattr__，无 Python 层 __setattr__），
    #    故整个读-改-写之间不存在可切换点。
    # 3. 探针的判别力是验证过的，不是假设：同一个 `+= 1`，把 STORE_ATTR 换成
    #    Python 层 __setattr__ 后立刻丢 30%~40%（见
    #    docs/audit/2026-10-07-batches/probes/g5/probe_worker_faults_race.py 的 B 段
    #    阳性对照）；而真实形态在 switchinterval 调到 1e-9 时仍是 0 丢。
    #
    # 什么时候需要重新评估：若将来 free-threading（无 GIL）构建、或把该字段改成
    #    property / 加 __setattr__，上述前提就不再成立，应改用 `itertools.count`
    #    或加锁。`test_worker_faults_counter_has_no_race.py` 会在前提被破坏时转红。
    worker_faults: int = 0
    fired_reminders: list = field(default_factory=list)
    # ---- 对话闭环的录音/超时状态（net↔audio 协作）----
    _rec_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _rec_ready: threading.Event = field(default_factory=threading.Event, repr=False)
    _last_recording: Any = field(default=None, repr=False)
    _last_recording_gen: int = -1
    _rec_started_gen: int = -1
    _thinking_since_ms: int | None = None
    _recording_since_ms: int | None = None
    _rec_entry_mark: int = -1

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

    def role_summaries(self) -> list[dict[str, Any]]:
        return self.roles.summaries()

    def action_files(self) -> list[dict[str, Any]]:
        return self.roles.action_files()

    # ---------- 角色切换 ----------
    def switch_active_role(self, role_id: int) -> dict[str, Any]:
        """切换激活角色；THINKING 中先**取消本轮**再切（见 :meth:`_abort_thinking_round`）。

        **为什么 THINKING 不进拒绝列表**：换角色本来就会让旧回包作废（代数 +1），
        这一轮对话**已经作废**，继续等 AI 回答毫无意义。而「拒绝」是把一个本可立刻
        完成的操作变成用户的负担 —— 用户在等回答时最想换的恰恰就是角色。
        """
        with self._lock:
            if self.motion_busy or self.state_machine.state in (State.PERFORMING, State.RECORDING):
                raise ConflictError("当前状态不允许切换角色（正在运动或录音）")
            self._abort_thinking_round()
            config = self.roles.set_active(int(role_id))  # 切换即 session_generation += 1
            return {"role_id": config.role_id, "config_version": config.config_version}

    def _abort_thinking_round(self) -> None:
        """THINKING 期间换角色：先走既有 ``DIALOG_PRESS`` 迁移取消本轮，让状态立即收敛。

        **修的是什么（实测复现）**：守卫只拦 ``PERFORMING`` / ``RECORDING``，不含
        ``THINKING``。于是换角色在等待期被放行、代数 +1 使旧回包作废，但**没人结束
        这一轮** —— 设备静默停在 THINKING，直到 ``NET_THINKING_TIMEOUT_S``（60s）
        的 tick 兜底才回退 AI_FAIL。换一次角色白等一分钟。

        **为什么用「取消」而不是「拒绝」**：见 :meth:`switch_active_role` 的说明。

        **为什么能安全地顺带收敛**（逐条实测，见报告）：
        - ``(THINKING, DIALOG_PRESS) -> IDLE`` 是状态表里现成的迁移，且**进入 IDLE
          本身就会代数 +1**，所以「取消 + 换角色」共 +2；
        - 代数只被用于 ``!=`` 相等比较（陈旧回包判定、命令丢弃），全仓**没有**任何
          处做代数的相对运算、也没有以代数为下标的容器，故 +2 与 +1 语义等价；
        - 该迁移的下发命令是 ``STOP_AUDIO`` / ``STOP_MOTION``，与「本轮作废」一致。

        **只处理 THINKING，不动 SPEAKING**：``SPEAKING`` 的出边里没有「取消本轮」这个
        概念 —— ``(SPEAKING, DIALOG_PRESS)`` 指向 ``RECORDING``（先停播再录），
        拿它来「取消」会直接开始录音，把静默滞留换成更糟的误触发。而 SPEAKING 本来
        就有确定性出口：``PLAY_DONE`` **不在** ``STALE_SENSITIVE`` 里，且
        ``_play_clip`` 无条件回投它（clip 为 None 也回投），所以换角色后仍会自行
        收敛到 IDLE，不存在 60s 静默。
        """
        if self.state_machine.state is not State.THINKING:
            return
        # 先预判再动手：迁移不存在时不留下「什么都没发生」的静默失败。
        if self.state_machine.destination(Trigger.DIALOG_PRESS) is not State.IDLE:
            return
        self._deliver(self.engine.apply(Trigger.DIALOG_PRESS, {"reason": "role_switch"}))

    # ---------- 动作校验 / 播放 / 停止 ----------
    def role_limits(self, role_id: Any) -> tuple[int, int]:
        """取该角色声明的限幅；取不到时**回落到固件保守区间**而不是报错。

        为什么回落而不是抛异常：限幅校验是**收紧型**加固，作用是拦住越限幅的动作，
        不该反过来让一个本来能播的动作播不出来（新的拒绝理由 = 新的用户可见故障）。
        取不到角色配置只说明「这次比平时宽松」，而固件区间仍然拦得住。

        同时供 ``execution._run_play`` 下发前复核用 —— 入口校验与下发前复核是
        **两道独立防线**：前者给用户可读的错误，后者保证「绕过入口直接下发」也拦得住。
        """
        try:
            config = self.roles.get(int(role_id))
        except Exception:  # noqa: BLE001 角色未注册/配置异常都不该阻断播放
            return (FW_MIN_US, FW_MAX_US)
        return (int(config.min_us), int(config.max_us))

    def validate_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """按**该动作自称的 role_id** 取角色限幅校验（不是当前激活角色）。

        为什么用动作自己的 ``role_id``：本方法服务于「编辑态校验」—— 用户在网页上
        改完动作、还没保存播放，此时动作可能属于任意角色。按当前激活角色取限幅会
        拿 role2 的限幅去判 role1 的动作，产生「明明合法却被拒」的假阳性。
        动作里没有合法 ``role_id`` 时 ``validate`` 本身会先拒掉格式。
        """
        from host.motion.validation import validate  # noqa: PLC0415  （延迟导入，避免循环）

        role_id = action.get("role_id") if isinstance(action, dict) else None
        min_us, max_us = self.role_limits(role_id)
        try:
            validate(action, min_us=min_us, max_us=max_us)
        except (ValueError, TypeError) as exc:
            return {"valid": False, "errors": [{"path": "action", "message": str(exc)}]}
        return {"valid": True, "errors": []}

    def resolve_greet_action(self) -> dict[str, Any] | None:
        """把 core 打下的 ``greet`` 语义解析成当前角色的真实动作载荷。

        与 :meth:`play_action` 同源（``role.json`` 白名单 → ``actions/greet.json``
        → ``validate_action``），只是入口不同：这里没有请求方，来源是物理表演键。

        **任何一步不成立都返回 ``None``，绝不抛异常**：按键路径没有 409 入口，
        动作包缺失或非法若冒泡成异常，会被 ``_run_play`` 归类为 SAFETY_TRIP →
        设备进故障锁定（表现成「玩具坏了」），而实际只是内容问题。返回 ``None``
        让调用方按「本轮无动作」收尾回 IDLE，与既有空载荷语义一致。
        """
        active = self.roles.active_role_id
        if active is None:
            self._warn_greet_unresolved("未加载任何角色")
            return None
        path = self.roles.action_path(active, GREET_ACTION_ID)
        if path is None:
            self._warn_greet_unresolved(f"角色 {active} 的动作包缺少 {GREET_ACTION_ID}")
            return None
        try:
            action = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._warn_greet_unresolved(f"动作文件不可读: {path} ({exc})")
            return None
        verdict = self.validate_action(action)
        if not verdict["valid"]:
            self._warn_greet_unresolved(f"动作文件非法: {verdict['errors']}")
            return None
        self.roles.set_current_action(GREET_ACTION_ID)
        return {"action": action, "role_id": int(active), "action_id": GREET_ACTION_ID}

    def _warn_greet_unresolved(self, reason: str) -> None:
        """解析失败必须**可见**：否则表现为「按了没反应」，排障无从下手。"""
        if self.log is not None:
            self.log.warning("表演键打招呼动作未解析，本轮只做状态收尾：%s", reason)

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
            # **先预判、再动手**：`PERFORM_PRESS` 在 SPEAKING / RECORDING 下也是合法迁移
            # （都指向 IDLE），若直接 apply 会真的打断说话或取消录音，随后才因拿不到
            # PLAY_ACTION 命令抛 409 —— 于是「拒绝」变成了「拒绝 + 已经改了状态」。
            landing = self.state_machine.destination(Trigger.PERFORM_PRESS)
            if landing is not State.PERFORMING:
                raise ConflictError(
                    f"当前状态 {self.state_machine.state.value} 不接受播放指令"
                )
            path = self.roles.action_path(active, action_id)
            if path is None:
                raise ConflictError(f"动作文件不存在: {action_id}")
            action = json.loads(Path(path).read_text(encoding="utf-8"))
            verdict = self.validate_action(action)
            if not verdict["valid"]:
                # 动作文件非法必须在**入口**拒绝。否则会一路下发到 `MotionClient.play` 的
                # `validate()` 抛错，被 `_run_play` 归类为 SAFETY_TRIP → 设备直接进故障锁定，
                # 「文件写错了」的表现变成「玩具坏了、要人工解除」。
                raise ConflictError(f"动作文件非法: {verdict['errors']}")
            self.roles.set_current_action(action_id)
            commands = self.engine.apply(
                Trigger.PERFORM_PRESS,
                {"action": action, "role_id": active, "action_id": action_id},
            )
            self._deliver(commands)
            self.motion_busy = any(c.kind is CommandKind.PLAY_ACTION for c in commands)
            if not self.motion_busy:
                # 预判已保证目标态是 PERFORMING、必然下发 PLAY_ACTION。走到这里说明
                # 预判与命令表不一致（装配问题），如实抛出而不是伪装成 409。
                raise HostError("装配异常：预判可播放，但未下发 PLAY_ACTION 命令")
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
