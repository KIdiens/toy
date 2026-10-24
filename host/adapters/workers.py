"""领域工作线程：net（ASR/LLM/TTS）、audio（录放）、tick（提醒到期 + THINKING 超时）。

背景（P0-1）：核心会发出 NET_REQUEST / RECORD_START / PLAY_AUDIO 等命令，但此前
只有 motion 一条消费路径，其余命令被静默丢弃 → 按键对话永久卡在 THINKING。本模块
补齐三类领域消费者，并把结果**以事件回投事件总线**（核心仍是唯一状态机持有者）。

纪律：
- 每个队列**恰好一个消费者线程**；线程只投事件、不改状态机（architecture §3）。
- 所有领域异常都在循环内捕获并记日志，**绝不让领域线程静默死亡**（P0-2 同源纪律）。
- 回投事件一律携带 ``generation``，供核心丢弃换角色/取消后的迟到回包（AC-15）。
"""
from __future__ import annotations

import threading
from typing import Any

from host.adapters.ai_responses import DOMAIN_POLL_S, AiResponsesMixin
from host.adapters.ai_responses import NET_ERROR_BACKOFF_S as NET_ERROR_BACKOFF_S
from host.adapters.ai_responses import RECORD_POLL_S as RECORD_POLL_S
from host.adapters.ai_responses import REPLY_WAIT_S as REPLY_WAIT_S
from host.adapters.audio_control import CANCEL_KEY
from host.adapters.shutdown import stop_domain_workers
from host.audio.recorder import Recorder
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import State

AUDIO_ERROR_BACKOFF_S = 0.2  # audio 线程同理
TICK_INTERVAL_S = 0.5
NET_THINKING_TIMEOUT_S = 60.0  # THINKING 最长驻留（qwen 单次超时 60s）
WORKER_JOIN_TIMEOUT_S = 2.0
DEFAULT_RECORD_LIMIT_S = 15.0
FIRED_HISTORY_LIMIT = 50
_RECORD_CAP_EPS_S = 0.05  # 判定「是否触顶录满」的容差
MAX_SIM_SPEAK_S = 3.0  # 无硬件档位模拟「说话中」的时长上限


class WorkersMixin(AiResponsesMixin):
    """领域线程的启停与消费循环。

    依赖宿主提供：``bus`` / ``state_machine`` / ``clock`` / ``router`` / ``recorder`` /
    ``player`` / ``scheduler`` / ``log`` / ``net_queue`` / ``audio_queue`` /
    ``worker_faults`` / ``fired_reminders`` / ``_rec_*`` 等状态字段。
    """

    # ---------- 启停 ----------
    def start_workers(self) -> None:
        """启动 net / audio / tick 三个领域线程（各队列恰好一个消费者）。"""
        self._worker_stop = threading.Event()
        for name, target in (
            ("net", self._net_loop),
            ("audio", self._audio_loop),
            ("tick", self._tick_loop),
        ):
            thread = threading.Thread(
                target=self._run_domain, args=(target, self._worker_stop),
                name=f"host-{name}", daemon=True
            )
            self._domain_threads.append(thread)
            thread.start()

    def _run_domain(self, target, stop_event) -> None:
        try:
            target(stop_event)
        finally:
            self.db.close()  # SQLite 连接只能由创建它的线程关闭。

    def register_motion_thread(self, thread) -> None:
        """登记 motion worker（``run_forever``）线程引用，供存活探针判活。

        为什么不自动起：这个线程由 ``main.py`` 与 uvicorn 主循环一起组装
        （它需要 stop_event 与生命周期收尾），不归 ``start_workers`` 管。
        但**必须**登记：不登记的话 ``worker_alive`` 只能退回心跳判定，而心跳
        写在 ``run_forever`` 循环开头、播放期间全程不更新，会把「正在播放」
        误报成「motion worker 已死」（wave.json 4000ms 必然越过 5s 阈值）。
        """
        self._motion_thread = thread

    def stop_workers(self) -> None:
        """请求停止并 join 全部领域线程（幂等）。"""
        stop_domain_workers(self, timeout=WORKER_JOIN_TIMEOUT_S)

    # ---------- audio worker ----------
    def _audio_loop(self, stop_event: threading.Event) -> None:
        """audio 队列消费者；与 net 同纪律，取件本身也必须在兜底内。"""
        while not stop_event.is_set():
            command = None
            try:
                command = self.audio_queue.get(timeout=DOMAIN_POLL_S)
                if command is None:
                    continue
                self._handle_audio_command(command)
            except Exception as exc:  # noqa: BLE001  线程存活优先于单次失败
                self._on_worker_error("audio", exc)
                if command is not None:
                    self._fail_audio(command, exc)
                stop_event.wait(AUDIO_ERROR_BACKOFF_S)

    def _fail_audio(self, command: Command, exc: Exception) -> None:
        phase = {CommandKind.RECORD_START: "record",
                 CommandKind.PLAY_AUDIO: "play"}.get(command.kind)
        if phase is None:
            return
        with self._lock:
            if self.audio_control is not None and not self.audio_control.is_current(command):
                return
            cancel = command.payload.get(CANCEL_KEY)
            if phase == "play" and cancel is not None and cancel.is_set():
                return  # 已被用户打断的旧播放失败，不结束新录音。
            # 正在说话时允许换角色但不终止声音；当前播放的失败也必须能结束它。
            generation = self.state_machine.generation if phase == "play" else command.generation
            self._publish(EventKind.AUDIO_FAIL, {"phase": phase, "reason": str(exc)}, generation)

    def _handle_audio_command(self, command: Command) -> None:
        kind = command.kind
        if kind is CommandKind.RECORD_START:
            self._record_once(command.generation, command.payload.get(CANCEL_KEY))
        elif kind is CommandKind.RECORD_STOP:
            self._finish_recording(command.generation)
        elif kind is CommandKind.PLAY_AUDIO:
            self._play_clip(command)
        elif kind is CommandKind.STOP_AUDIO:
            if self.audio_control is None:
                self.player.stop()  # 旧装配兼容；新装配的停止信号已经在入队时发出。
        elif kind is CommandKind.VOLUME_STEP:
            # 先算目标档位、交给 `patch_settings` 统一「落库 + set_level」：
            # 写库失败时档位保持不变，不会出现「档位已变、设置未落库」的分叉；
            # 失败由 `_audio_loop` 的兜底 except 记成 worker_faults（可见，非静默）。
            self.patch_settings({"volume_level": self.player.peek_next_level()})
            self._log_info("音量档位 -> %s", self.player.level)
        else:
            self._warn_unhandled("audio", command)

    def _record_once(self, generation: int, cancel=None) -> None:
        with self._rec_lock:
            self._rec_ready.clear()
            self._rec_started_gen = generation
            self._recording_error = None
        try:
            if isinstance(self.recorder, Recorder):
                recording = self.recorder.record(DEFAULT_RECORD_LIMIT_S, cancel=cancel)
            else:
                recording = self.recorder.record(DEFAULT_RECORD_LIMIT_S)
        except Exception as exc:
            with self._rec_lock:
                self._recording_error = exc
                self._last_recording = None
                self._last_recording_gen = generation
                self._last_recording_cancel = cancel
                self._rec_ready.set()
            raise
        with self._rec_lock:
            self._last_recording = recording
            self._last_recording_gen = generation
            self._last_recording_cancel = cancel
            self._rec_ready.set()
        # 仅当「录满上限」时自动结束（用户不会再来按第二下）；否则由用户按键结束，
        # 避免录音一结束就抢先 REC_STOP→THINKING 而把随后的一次按键误判成取消。
        duration = float(getattr(recording, "duration_s", 0.0) or 0.0)
        if duration >= DEFAULT_RECORD_LIMIT_S - _RECORD_CAP_EPS_S:
            self._publish(EventKind.REC_STOP, {}, generation)

    def _finish_recording(self, generation: int) -> None:
        with self._rec_lock:
            done = self._rec_ready.is_set() and self._rec_started_gen == generation
        if not done and self.audio_control is None:
            self._record_once(generation)  # 兜底：RECORD_START 未到/未完成

    def _play_clip(self, command: Command) -> None:
        cancel = command.payload.get(CANCEL_KEY)
        if cancel is not None and cancel.is_set():
            return
        clip = command.payload.get("clip")
        duration_s = 0.0
        try:
            if clip is not None:
                duration_s = float(getattr(clip, "duration_s", 0.0) or 0.0)
                self.player.play(clip, cancel=cancel)
            if self.simulate and self.player.backend.name == "null" and duration_s > 0.0:
                if cancel is not None:
                    cancel.wait(min(duration_s, MAX_SIM_SPEAK_S))
                else:
                    self.clock.sleep(min(duration_s, MAX_SIM_SPEAK_S))
        finally:
            self.player.stop()
        if cancel is None or not cancel.is_set():
            self._publish(EventKind.PLAY_DONE, {}, command.generation)

    # ---------- tick worker：提醒到期 + THINKING 超时 ----------
    def _tick_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._fire_due_reminders()
                self._enforce_thinking_timeout()
                self._enforce_recording_timeout()
            except Exception as exc:  # noqa: BLE001
                self._on_worker_error("tick", exc)
            # 固定节拍：异常时也照常等待，避免"每轮都失败 → 空转打满 CPU"。
            stop_event.wait(TICK_INTERVAL_S)

    def _enforce_recording_timeout(self) -> None:
        """RECORDING 驻留上限兜底：超时即替用户结束本轮录音。

        为什么必须有：自动收尾的唯一判据是「录满 ``DEFAULT_RECORD_LIMIT_S``」，而
        ``NullAudioBackend`` 只录 2.0s、永远够不到上限——于是无硬件档位下按一次对话键
        再走开，设备会**永久停在 RECORDING**（实测 24s 仍在录）。真机档位同样需要这条：
        用户中途放下设备时不该把 recorder 一直占着。
        """
        now = self.clock.now_ms()
        if self.state_machine.state is not State.RECORDING:
            self._recording_since_ms = None
            self._rec_entry_mark = -1
            return
        # 会话键 = 「最后一次进入 RECORDING」的迁移在 history 里的下标。它由**状态机线程**
        # 同步维护，不受 audio 线程阻塞影响。
        # 不能用「tick 是否观测到过非 RECORDING」当清零条件：取消→IDLE 与重按→RECORDING
        # 若落在相邻两次 tick 之间（间隔 ≤ TICK_INTERVAL_S，实测 0.03s 就能发生），tick 从未
        # 见到 IDLE，旧起点被新一轮沿用 → 新一轮被立刻收尾、录音被静默截断。
        key = self._recording_session_mark()
        if self._recording_since_ms is None or key != self._rec_entry_mark:
            self._rec_entry_mark = key
            self._recording_since_ms = now
            return
        if now - self._recording_since_ms >= int(self._record_limit_s() * 1000):
            self._recording_since_ms = None
            self._rec_entry_mark = -1
            self._log_info("RECORDING 驻留超时 %.0fs，自动结束本轮录音",
                           self._record_limit_s())
            self._publish(EventKind.REC_STOP, {"auto": "timeout"},
                          self.state_machine.generation)

    def _recording_session_mark(self) -> int:
        """最后一次「进入 RECORDING」的迁移下标；历史里没有则返回 -1。

        从尾部倒扫——当前就处在 RECORDING，命中通常落在最后几条，近似 O(1)。
        """
        history = self.state_machine.history
        for index in range(len(history) - 1, -1, -1):
            result = history[index]
            if result.ok and result.dest is State.RECORDING:
                return index
        return -1

    def _record_limit_s(self) -> float:
        """录音上限秒数：以 recorder 的策略为准，缺失时回落到默认值。"""
        policy = getattr(self.recorder, "policy", None)
        return float(getattr(policy, "max_seconds", None) or DEFAULT_RECORD_LIMIT_S)

    def _fire_due_reminders(self) -> None:
        if self.scheduler is None:
            return
        for reminder in self.scheduler.fire_due():
            self.fired_reminders.append(
                {"id": reminder.id, "text": reminder.text, "due_at_utc": reminder.due_at_utc}
            )
            del self.fired_reminders[:-FIRED_HISTORY_LIMIT]
            self._log_info("提醒触发：id=%s text=%s", reminder.id, reminder.text)

    def _enforce_thinking_timeout(self) -> None:
        now = self.clock.now_ms()
        if self.state_machine.state is not State.THINKING:
            self._thinking_since_ms = None
            return
        if self._thinking_since_ms is None:
            self._thinking_since_ms = now
            return
        if now - self._thinking_since_ms >= int(NET_THINKING_TIMEOUT_S * 1000):
            self._thinking_since_ms = None
            self._log_info("THINKING 驻留超时 %.0fs，回退 AI_FAIL", NET_THINKING_TIMEOUT_S)
            self._publish(EventKind.AI_FAIL, {"reason": "网络请求超时"},
                          self.state_machine.generation)

    # ---------- 公共小工具 ----------
    def _publish(self, kind: EventKind, payload: dict[str, Any], generation: int) -> None:
        self.bus.publish(
            Event(kind, payload=payload, ts_ms=self.clock.now_ms(), generation=generation)
        )

    def _on_worker_error(self, worker: str, exc: Exception) -> None:
        # 裸自增实测不丢计数（同 execution.py 的注释与 ports.py 的字段说明）。
        # 只被 /status 读作诊断显示，无逻辑分支依赖其值，故不加锁。
        self.worker_faults += 1
        if self.log is not None:
            self.log.error("[%s worker] 异常（已捕获，线程继续运行）：%s",
                           worker, exc, exc_info=True)

    def _warn_unhandled(self, worker: str, command: Command) -> None:
        if self.log is not None:
            self.log.warning("[%s worker] 收到未处理命令 %s，已丢弃", worker, command.kind.value)

    def _log_info(self, message: str, *args: Any) -> None:
        if self.log is not None:
            self.log.info(message, *args)
