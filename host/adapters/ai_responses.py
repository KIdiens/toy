"""网络领域线程：录音等待、会话守卫与 AI 结果回投；不直接操作设备。"""
from __future__ import annotations

import threading
from typing import Any

from host.audio.backend import recording_wav
from host.core.errors import AiCancelled
from host.core.events import Command, CommandKind, EventKind

DOMAIN_POLL_S = 0.05
NET_ERROR_BACKOFF_S = 0.2
RECORD_POLL_S = 0.02
REPLY_WAIT_S = 16.0  # 录音就绪兜底，须长于单次录制上限。


class AiResponsesMixin:
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
        except AiCancelled as exc:
            self._log_info('net worker 结束已失效对话：%s', exc)
            self._publish(EventKind.AI_FAIL, {'reason': str(exc)}, generation)
            return
        except Exception as exc:  # noqa: BLE001  领域线程不得因单次失败而退出
            self._on_worker_error("net", exc)
            self._publish(EventKind.AI_FAIL, {"reason": f"网络/推理失败: {exc}"}, generation)
            return
        if result is None or (result.blocked and not getattr(result, 'refusal_ready', False)):
            reason = ("本轮录音已取消或没有可用数据" if result is None else
                      getattr(result, "reason", "") or "回复被安全检查拦截")
            self._log_info("net worker 判定失败：%s", reason)
            self._publish(EventKind.AI_FAIL, {"reason": reason}, generation)
            return
        self._publish(
            EventKind.AI_DONE,
            {
                "clip": result.clip,
                "reply_text": result.reply_text,
                "user_text": result.user_text,
                "performances": getattr(result, 'performances', ()),
                "blocked": result.blocked,
                "reason": getattr(result, 'reason', ''),
            },
            generation,
        )

    def _respond(self, command: Command) -> Any:
        """取本轮录音（若就绪）→ 交给 AiRouter 做 ASR→审核→LLM→工具→审核→TTS。"""
        if self.router is None:
            raise RuntimeError("AI 路由未装配（context.router 为空）")
        recording = self._await_recording(command.generation)
        if recording is None or not recording.data:
            return None  # 取消或空录音不请求云端，也不计作领域线程故障。
        wav = recording_wav(recording)
        if self.tool_performer is None:
            return self.router.respond_audio(wav)
        with self.tool_performer.request(command.generation):
            return self.router.respond_audio(wav)

    def _await_recording(self, generation: int) -> Any:
        """等待本轮录音就绪；若本轮从未开始录音 / 已被新一轮取代则立即返回 None。

        **每轮都要复查 ``_rec_started_gen``**（P1，实测复现）：只在进入时查一次时，
        旧请求进入等待后用户又按了一次开始录音 → ``_record_once`` 会 ``_rec_ready.clear()``
        并把 ``_rec_started_gen`` 改成新代数 → 旧请求的
        ``_last_recording_gen == generation`` 永远不成立，白白等满 ``REPLY_WAIT_S``（16s）。
        net 是单消费者，排在后面的 NET_REQUEST 全部被堵住，整套对话链路卡死。

        返回 ``None`` 只会让 ``_respond`` 走「这轮没有录音」的既有分支，不会误把别的
        代数的录音当成自己的 —— 因为取数据仍然要求 ``_last_recording_gen == generation``。
        """
        with self._rec_lock:
            if not self._expects_recording(generation):
                return None
        deadline = self.clock.now_ms() + int(REPLY_WAIT_S * 1000)
        while self.clock.now_ms() < deadline:
            with self._rec_lock:
                if not self._expects_recording(generation):
                    # 已被新一轮录音取代：本轮不可能再就绪，立即收口，别占着 net 线程
                    return None
                operation = self.audio_control.recording if self.audio_control is not None else None
                current = operation is None or self._last_recording_cancel is operation[1]
                if current and self._rec_ready.is_set() and self._last_recording_gen == generation:
                    if self._recording_error is not None:
                        raise self._recording_error
                    return self._last_recording
            self.clock.sleep(RECORD_POLL_S)
        return None

    def _expects_recording(self, generation: int) -> bool:
        if self.audio_control is not None:
            if self.audio_control.closed:
                return False
            operation = self.audio_control.recording
            if operation is not None:
                return operation[0] == generation
        return self._rec_started_gen == generation
