"""AI 播放计划：网络线程准备，安全回复结束后由核心串行派发。"""
from __future__ import annotations

import wave
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from host.adapters.action_documents import prepare_play_document, read_document
from host.ai.providers import AudioClip
from host.ai.tool_scope import MAX_EFFECTS, defer_effect, request_metadata, request_scope
from host.audio.backend import Recording, recording_wav
from host.audio.catalog import analyze_pcm16, read_wav_pcm
from host.core.engine import default_dispatcher
from host.core.errors import ConflictError, ToolValidationError
from host.core.events import CommandKind, EventKind
from host.core.state_machine import BUTTON_VOLUME, State, Trigger


class ToolPerformer:
    def __init__(self, context):
        self.context = context
        self.pending: deque = deque()
        self.generation = None

    @contextmanager
    def request(self, generation):
        context = self.context
        with context._lock:
            role = context.roles.active()
            metadata = {'context_id': id(context), 'generation': generation,
                        'role_id': role.role_id if role else None,
                        'config_version': role.config_version if role else None}
        with request_scope(lambda: self._current(metadata), metadata):
            yield

    def _current(self, metadata):
        context = self.context
        with context._lock:
            role = context.roles.active()
            if metadata is None:
                return False
            same_role = ((role is None and metadata.get('role_id') is None)
                         or (role is not None and role.role_id == metadata.get('role_id')
                             and role.config_version == metadata.get('config_version')))
            return (metadata.get('context_id') == id(context)
                    and not context._shutdown.is_set() and same_role
                    and context.state_machine.state is State.THINKING
                    and context.state_machine.generation == metadata.get('generation'))

    def _metadata(self):
        metadata = request_metadata()
        if not self._current(metadata):
            raise ToolValidationError('播放请求不属于当前有效对话')
        if metadata['role_id'] is None:
            raise ToolValidationError('未加载角色，不能安排播放任务')
        return metadata

    def prepare_action(self, action_id):
        metadata = self._metadata()
        try:
            document = read_document(self.context, metadata['role_id'], action_id)
        except ConflictError as exc:
            raise ToolValidationError(str(exc)) from exc
        if document['config_version'] != metadata['config_version']:
            raise ToolValidationError('角色配置已变化，请重新请求')
        defer_effect({'kind': 'action', 'role_id': metadata['role_id'],
                      'config_version': metadata['config_version'],
                      'action_id': action_id, 'action': document['action']})
        return {'ok': True, 'action_id': action_id, 'accepted': True,
                'scheduled': False, 'pending_reply': True}

    def prepare_content(self, item_id):
        metadata = self._metadata()
        try:
            item = self.context.catalog.get(item_id)
            if item is None:
                raise ValueError('本地内容不存在')
            if not Path(item.path).resolve().is_relative_to(self.context.catalog.root.resolve()):
                raise ValueError('内容路径不在本地内容库内')
            pcm, rate, channels = read_wav_pcm(item.path)
            if rate <= 0 or channels not in (1, 2):
                raise ValueError('本地内容须为单声道或双声道 PCM WAV')
            report = analyze_pcm16(pcm, channels=channels)
            if not report.ok:
                raise ValueError(report.reason)
            clip = AudioClip(recording_wav(Recording(pcm, rate, channels)), rate, channels)
        except (OSError, ValueError, wave.Error) as exc:
            raise ToolValidationError(f'本地内容不可播放：{exc}') from exc
        defer_effect({'kind': 'content', 'role_id': metadata['role_id'],
                      'config_version': metadata['config_version'],
                      'item_id': item_id, 'clip': clip})
        return {'ok': True, 'item_id': item_id, 'duration_s': clip.duration_s,
                'accepted': True, 'scheduled': False, 'pending_reply': True}

    def cancel(self):
        self.pending.clear()
        self.generation = None

    def dispatch(self, engine, event):
        context = self.context
        with context._lock:
            source, generation = engine.state_machine.state, engine.state_machine.generation
            if context._shutdown.is_set():
                self.cancel()
                return []
            if self.generation != generation:
                self.cancel()
            if (event.kind is EventKind.AI_DONE and source is State.THINKING
                    and event.generation == generation):
                plans = event.payload.get('performances', ())
                self.cancel()
                if isinstance(plans, (list, tuple)) and len(plans) <= MAX_EFFECTS:
                    self.pending.extend(deepcopy(plans))
                    self.generation = generation
            # 音量键不取消计划；其他按键、安全故障、设备异常均取消余下任务。
            if ((event.kind is EventKind.BUTTON and event.payload.get('n') != BUTTON_VOLUME)
                    or (event.kind is EventKind.AUDIO_FAIL and event.generation == generation)
                    or event.kind in (EventKind.SAFETY_TRIP, EventKind.MCU_FAULT,
                                      EventKind.MCU_ERROR, EventKind.SHUTDOWN)):
                self.cancel()
            completed = (event.generation == generation and (
                (event.kind is EventKind.PLAY_DONE and source is State.SPEAKING)
                or (event.kind is EventKind.PERFORM_DONE and source is State.PERFORMING)))
            commands = default_dispatcher(engine, event)
            if engine.state_machine.state is State.FAULT_LOCKED:
                self.cancel()
            if self.pending and engine.state_machine.generation != self.generation:
                if completed and engine.state_machine.state is State.IDLE:
                    commands += self._next(engine)
                else:
                    self.cancel()
            return commands

    def _next(self, engine):
        context = self.context
        effect = self.pending.popleft()
        try:
            role = context.roles.active()
            if (not isinstance(effect, dict) or role is None
                    or effect.get('role_id') != role.role_id
                    or effect.get('config_version') != role.config_version):
                raise ConflictError('角色或配置已变化')
            if effect.get('kind') == 'action':
                action_id = effect['action_id']
                if action_id not in context.roles.actions_for(role.role_id):
                    raise ConflictError('动作已不在当前角色白名单中')
                action = prepare_play_document(context, role.role_id, action_id,
                                               role.config_version, effect['action'])
                seq = context._begin_play()
                payload = {'action': action, 'role_id': role.role_id,
                           'action_id': action_id, 'play_seq': seq}
                commands = engine.apply(Trigger.PERFORM_PRESS, payload)
                if not any(command.kind is CommandKind.PLAY_ACTION for command in commands):
                    context._end_play(seq)
                    raise ConflictError('当前状态不接受工具动作')
                context.roles.set_current_action(action_id)
                context.action_progress = {'action_id': action_id, 'seq': 0, 'progress_ms': 0}
            elif effect.get('kind') == 'content' and isinstance(effect.get('clip'), AudioClip):
                commands = engine.apply(Trigger.LOCAL_PLAY, {'clip': effect['clip'],
                                                            'item_id': effect['item_id']})
            else:
                raise ConflictError('未知或非法播放计划')
            self.generation = engine.state_machine.generation
            return commands
        except (ConflictError, KeyError, TypeError, ValueError) as exc:
            self.cancel()
            if context.log is not None:
                context.log.warning('AI 播放计划已取消：%s', exc)
            return []
