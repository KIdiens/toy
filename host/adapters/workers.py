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

from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import State

DOMAIN_POLL_S = 0.05  # 领域队列取件超时（让线程定期回到 stop 判定）
NET_ERROR_BACKOFF_S = 0.2  # net 线程异常后的退避，避免底层持续故障时空转打满 CPU
AUDIO_ERROR_BACKOFF_S = 0.2  # audio 线程同理
TICK_INTERVAL_S = 0.5
RECORD_POLL_S = 0.02
REPLY_WAIT_S = 16.0  # 等录音完成的兜底上限（> 单次录制上限）
NET_THINKING_TIMEOUT_S = 60.0  # THINKING 最长驻留（qwen 单次超时 60s）
WORKER_JOIN_TIMEOUT_S = 2.0
DEFAULT_RECORD_LIMIT_S = 15.0
FIRED_HISTORY_LIMIT = 50
_RECORD_CAP_EPS_S = 0.05  # 判定「是否触顶录满」的容差
MAX_SIM_SPEAK_S = 3.0  # 无硬件档位模拟「说话中」的时长上限


class WorkersMixin:
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
                target=target, args=(self._worker_stop,), name=f"host-{name}", daemon=True
            )
            thread.start()
            self._domain_threads.append(thread)

    def stop_workers(self) -> None:
        """请求停止并 join 全部领域线程（幂等）。"""
        if self._worker_stop is not None:
            self._worker_stop.set()
        for thread in list(self._domain_threads):
            thread.join(timeout=WORKER_JOIN_TIMEOUT_S)
        self._domain_threads.clear()

    # ---------- net worker ----------
    def _net_loop(self, stop_event: threading.Event) -> None:
        """net 队列消费者。

        **外层 try/except 是硬要求**（P0-2 同源纪律）：``net_queue.get()`` 自身抛错、
        或 ``_handle_net_request`` 之外的代码抛错（宿主被换掉、总线已关闭等）时，
        本线程会**直接静默死亡**——而 net 一死，NET_REQUEST 再无人消费，按对话键
        永久停在 THINKING，正是 P0-1 修掉的症状。任何异常都必须记日志并继续循环。
        """
        while not stop_event.is_set():
            try:
                command = self.net_queue.get(timeout=DOMAIN_POLL_S)
                if command is None or command.kind is not CommandKind.NET_REQUEST:
                    if command is not None:
                        self._warn_unhandled("net", command)
                    continue
                self._handle_net_request(command)
            except Exception as exc:  # noqa: BLE001  线程存活优先于单次失败
                self._on_worker_error("net", exc)
                stop_event.wait(NET_ERROR_BACKOFF_S)

    def _handle_net_request(self, command: Command) -> None:
        generation = command.generation
        if generation != self.state_machine.generation:
            self._log_info("net worker 丢弃迟到请求 gen=%s（当前 %s）",
                           generation, self.state_machine.generation)
            return
        try:
            result = self._respond(command)
        except Exception as exc:  # noqa: BLE001  领域线程不得因单次失败而退出
            self._on_worker_error("net", exc)
            self._publish(EventKind.AI_FAIL, {"reason": f"网络/推理失败: {exc}"}, generation)
            return
        if result is None or result.blocked:
            reason = getattr(result, "reason", "") or "回复被安全检查拦截"
            self._log_info("net worker 判定失败：%s", reason)
            self._publish(EventKind.AI_FAIL, {"reason": reason}, generation)
            return
        self._publish(
            EventKind.AI_DONE,
            {
                "clip": result.clip,
                "reply_text": result.reply_text,
                "user_text": result.user_text,
            },
            generation,
        )

    def _respond(self, command: Command) -> Any:
        """取本轮录音（若就绪）→ 交给 AiRouter 做 ASR→审核→LLM→工具→审核→TTS。"""
        if self.router is None:
            raise RuntimeError("AI 路由未装配（context.router 为空）")
        recording = self._await_recording(command.generation)
        wav = recording.data if recording is not None else b""
        return self.router.respond_audio(wav)

    def _await_recording(self, generation: int) -> Any:
        """等待本轮录音就绪；若本轮从未开始录音则立即返回 None（不空等）。"""
        with self._rec_lock:
            if self._rec_started_gen != generation:
                return None
        deadline = self.clock.now_ms() + int(REPLY_WAIT_S * 1000)
        while self.clock.now_ms() < deadline:
            with self._rec_lock:
                if self._rec_ready.is_set() and self._last_recording_gen == generation:
                    return self._last_recording
            self.clock.sleep(RECORD_POLL_S)
        return None

    # ---------- audio worker ----------
    def _audio_loop(self, stop_event: threading.Event) -> None:
        """audio 队列消费者；与 net 同纪律，取件本身也必须在兜底内。"""
        while not stop_event.is_set():
            try:
                command = self.audio_queue.get(timeout=DOMAIN_POLL_S)
                if command is None:
                    continue
                self._handle_audio_command(command)
            except Exception as exc:  # noqa: BLE001  线程存活优先于单次失败
                self._on_worker_error("audio", exc)
                stop_event.wait(AUDIO_ERROR_BACKOFF_S)

    def _handle_audio_command(self, command: Command) -> None:
        kind = command.kind
        if kind is CommandKind.RECORD_START:
            self._record_once(command.generation)
        elif kind is CommandKind.RECORD_STOP:
            self._finish_recording(command.generation)
        elif kind is CommandKind.PLAY_AUDIO:
            self._play_clip(command)
        elif kind is CommandKind.STOP_AUDIO:
            self.player.stop()
        elif kind is CommandKind.VOLUME_STEP:
            # 先算目标档位、交给 `patch_settings` 统一「落库 + set_level」：
            # 写库失败时档位保持不变，不会出现「档位已变、设置未落库」的分叉；
            # 失败由 `_audio_loop` 的兜底 except 记成 worker_faults（可见，非静默）。
            self.patch_settings({"volume_level": self.player.peek_next_level()})
            self._log_info("音量档位 -> %s", self.player.level)
        else:
            self._warn_unhandled("audio", command)

    def _record_once(self, generation: int) -> None:
        with self._rec_lock:
            self._rec_ready.clear()
            self._rec_started_gen = generation
        recording = self.recorder.record(DEFAULT_RECORD_LIMIT_S)
        with self._rec_lock:
            self._last_recording = recording
            self._last_recording_gen = generation
            self._rec_ready.set()
        # 仅当「录满上限」时自动结束（用户不会再来按第二下）；否则由用户按键结束，
        # 避免录音一结束就抢先 REC_STOP→THINKING 而把随后的一次按键误判成取消。
        duration = float(getattr(recording, "duration_s", 0.0) or 0.0)
        if duration >= DEFAULT_RECORD_LIMIT_S - _RECORD_CAP_EPS_S:
            self._publish(EventKind.REC_STOP, {}, generation)

    def _finish_recording(self, generation: int) -> None:
        with self._rec_lock:
            done = self._rec_ready.is_set() and self._rec_started_gen == generation
        if not done:
            self._record_once(generation)  # 兜底：RECORD_START 未到/未完成

    def _play_clip(self, command: Command) -> None:
        clip = command.payload.get("clip")
        duration_s = 0.0
        if clip is not None:
            duration_s = float(getattr(clip, "duration_s", 0.0) or 0.0)
            self.player.play(clip)
        if self.simulate and duration_s > 0.0:
            # 无硬件档位：空后端不阻塞（Player 契约「空后端零等待」），这里按片段时长
            # 模拟「说话中」，使 SPEAKING 在 UI/端到端可被观测，时序与真机一致。
            self.clock.sleep(min(duration_s, MAX_SIM_SPEAK_S))
        # 无论有无音频载荷都回投 PLAY_DONE，避免 SPEAKING 因缺帧而卡死。
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
