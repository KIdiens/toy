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

from host.adapters.action_documents import prepare_play_document, read_document
from host.adapters.execution import GREET_ACTION_ID, ExecutionMixin
from host.adapters.injection import InjectionMixin
from host.adapters.services import StorageFacade
from host.adapters.shutdown import close_context
from host.adapters.status import WORKER_LIVENESS_MS as WORKER_LIVENESS_MS
from host.adapters.status import StatusMixin, TargetSample
from host.adapters.workers import WorkersMixin
from host.core.engine import Engine
from host.core.errors import ConflictError, HostError
from host.core.events import CommandKind
from host.core.state_machine import SessionStateMachine, State, Trigger
from host.motion.client import MotionClient, PlayResult
from host.roles.schema import FW_MAX_US, FW_MIN_US


@dataclass
class AppContext(ExecutionMixin, InjectionMixin, WorkersMixin, StorageFacade, StatusMixin):
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
    # ---- 播放令牌（P0：停止/替换后的在途命令判定）----
    # 为什么不是一个共享布尔：见 ``motion_busy`` 属性与 ``ExecutionMixin._begin_play``。
    # ``_play_seq`` 只增不减（单调），``_active_play_seq`` 指向「当前唯一合法的那次播放」；
    # 两者都只在 ``self._lock`` 内读写（RLock）。
    _play_seq: int = field(default=0, repr=False)
    _active_play_seq: int | None = field(default=None, repr=False)
    worker_running: bool = False
    fault: dict[str, str] | None = None
    action_progress: dict[str, Any] | None = None
    _target_sample: TargetSample | None = field(default=None, repr=False)
    last_result: PlayResult | None = None
    log: Any = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # ---- 领域工作线程（P0-1）：队列句柄、启停句柄、存活探针 ----
    net_queue: Any = None
    audio_queue: Any = None
    audio_control: Any = field(default=None, repr=False)
    _router: Any = field(default=None, repr=False)
    tool_performer: Any = field(default=None, repr=False)
    _worker_stop: Any = field(default=None, repr=False)
    _shutdown: threading.Event = field(default_factory=threading.Event, repr=False)
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
    _last_recording_cancel: Any = field(default=None, repr=False)
    _recording_error: Exception | None = field(default=None, repr=False)
    _rec_started_gen: int = -1
    _thinking_since_ms: int | None = None
    _recording_since_ms: int | None = None
    _rec_entry_mark: int = -1

    # ---------- 派生状态 ----------
    @property
    def motion_busy(self) -> bool:
        """是否有一次播放仍在途 —— **派生量，不可写**。

        P0 缺陷 1 的根因就是这个量曾经是可写的共享布尔：A 播放中被 B 替换后，A 播完
        走 ``_run_play`` 的陈旧分支，把 **B 的** busy 清成 False；worker 取到 B 时看到
        False → 当陈旧命令丢弃且不发 ``PERFORM_DONE`` → 永久卡 PERFORMING。

        现在「在途」只有唯一判据：``_active_play_seq`` 是否被激活。``_end_play`` 只在
        令牌仍是当前令牌时才清，因此旧播放结束**不可能**清掉新播放的忙标志。
        """
        return self._active_play_seq is not None

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

        **角色未加载必须判非法，不能回落到固件区间**（P2-2，实测）：``role_limits``
        的回落是给**播放路径**用的兜底，本方法复用它就会把「角色 2 根本没装」吞掉 ——
        ``roles=[1,3]`` 时 ``validate_action(role_id=2, 1405us)`` 返回 ``valid: True``，
        而 role2 的真实限幅是 1420–1580，这个结论是编出来的。没有 ``role.json`` 就
        没有机械限幅，如实报「未加载」比给一个假的 True 有用。

        代价（误伤面）：想在**未安装的角色**上预校验的作者会拿到 ``False``。这是事实
        陈述，不是误判；播放路径不受影响，见 :meth:`play_action` 与
        ``execution._run_play``（它们仍走 :meth:`role_limits` 的回落）。
        """
        from host.motion.validation import VALID_ROLES, validate  # noqa: PLC0415  （延迟导入，避免循环）

        role_id = action.get("role_id") if isinstance(action, dict) else None
        # 只对**格式合法**的 role_id 提前判定：非 int（含 bool/字符串/缺失）与超出
        # 1–3 的编号一律交给 ``validate`` 报「格式版本或角色编号错误」—— 否则诊断
        # 信息会退化成一句「角色 None/9 未加载」，把格式问题说成安装问题。
        if type(role_id) is int and role_id in VALID_ROLES and not self.roles.exists(role_id):
            return {
                "valid": False,
                "errors": [{
                    "path": "action.role_id",
                    "message": f"角色 {role_id} 未加载，无法按机械限幅校验",
                }],
            }
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
        # 与 ``play_action`` 同一条入口断言：动作自称的 role_id 必须等于激活角色。
        # 按键路径没有 409 入口，所以这里降级成「本轮无动作」而不是抛异常。
        if not self._action_matches_role(action, active):
            self._warn_greet_unresolved(
                f"动作文件 role_id={action.get('role_id')} 与激活角色 {active} 不一致")
            return None
        self.roles.set_current_action(GREET_ACTION_ID)
        return {"action": action, "role_id": int(active), "action_id": GREET_ACTION_ID}

    @staticmethod
    def _action_matches_role(action: dict[str, Any], role_id: int) -> bool:
        """动作文件自称的 ``role_id`` 必须等于要播它的角色。

        为什么需要这道断言（实测）：``validate_action`` 按**动作自称的 role_id** 取限幅
        （编辑态校验要支持任意角色的动作），而 ``_run_play`` 按**激活角色**取限幅复核。
        两者不一致时，一个「自称 role1、脉宽 1405」的动作放进 role3 目录（限幅
        1430–1570）会被入口判合法、在下发前被第二道防线判越限 → SAFETY_TRIP 锁机，
        而它本只是一次内容错误。在入口先拒绝，才符合「内容错误不锁机」的承诺。
        """
        try:
            return int(action.get("role_id")) == int(role_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    def _warn_greet_unresolved(self, reason: str) -> None:
        """解析失败必须**可见**：否则表现为「按了没反应」，排障无从下手。"""
        if self.log is not None:
            self.log.warning("表演键打招呼动作未解析，本轮只做状态收尾：%s", reason)

    def action_document(self, *, role_id: int, action_id: str) -> dict[str, Any]:
        return read_document(self, role_id, action_id)

    def play_action(self, *, role_id: int, action_id: str,
                    action_version: str | None = None,
                    action: dict[str, Any] | None = None,
                    replace_current: bool = False) -> dict[str, Any]:
        """校验 → 入队 → 置忙；真实运动由 worker 执行（同步返回，不阻塞 HTTP）。"""
        with self._lock:
            active = self.roles.active_role_id
            if active is None:
                raise ConflictError("未加载任何角色，无法播放")
            if int(role_id) != int(active):
                raise ConflictError(f"角色不匹配：请求 {role_id}，当前 {active}")
            if action is None and action_id not in self.roles.actions_for(active):
                raise ConflictError(f"非当前角色白名单动作: {action_id}")
            draft = action is not None
            action = prepare_play_document(self, active, action_id, action_version, action)
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
            if not draft:
                self.roles.set_current_action(action_id)
            # **先激活令牌、再入队**：worker 可能在本函数返回前就取到命令；若先入队
            # 后激活，它会看到「令牌不符」把这一条新命令当陈旧丢掉。整个 play_action
            # 都持 ``self._lock``（RLock），worker 的判定也在同一把锁内，不会插队。
            seq = self._begin_play()
            try:
                commands = self.engine.apply(
                    Trigger.PERFORM_PRESS,
                    {"action": action, "role_id": active, "action_id": action_id,
                     "play_seq": seq},
                )
                self._deliver(commands)
                if not any(c.kind is CommandKind.PLAY_ACTION for c in commands):
                    # 预判已保证目标态是 PERFORMING、必然下发 PLAY_ACTION。走到这里说明
                    # 预判与命令表不一致（装配问题），如实抛出而不是伪装成 409。
                    raise HostError("装配异常：预判可播放，但未下发 PLAY_ACTION 命令")
            except BaseException:
                # 没入队成功就不能留下「忙」的假象，否则之后所有停止/替换判定都会错。
                self._end_play(seq)
                raise
            self.action_progress = {"action_id": action_id, "seq": 0, "progress_ms": 0}
            return {"scheduled": True, "action_id": action_id, "seq": 0}

    def stop_action(self) -> dict[str, Any]:
        with self._lock:
            if self.tool_performer is not None:
                self.tool_performer.cancel()
            # **先让所有在途播放失效，再停机**：停止的语义是「这一刻之后不得再开播」。
            # 递增序号而不是只清当前令牌 —— 队列里可能还压着更早的 PLAY_ACTION，
            # 它们也必须一并作废（stop 不改代数，靠 generation 判不出来）。
            self._invalidate_plays()
            self.player.stop()
            state = self.state_machine.state
            triggers: list[Trigger] = []
            if state is State.PERFORMING:
                triggers.append(Trigger.PERFORM_PRESS)
            elif state is State.SPEAKING:
                triggers.append(Trigger.PLAY_DONE)
            elif state is State.THINKING:
                triggers.append(Trigger.DIALOG_PRESS)  # 停止也作废尚未返回的 AI 播放计划。
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
        close_context(self)
