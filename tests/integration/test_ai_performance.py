"""AI 的播放工具须真正交给执行队列，审核拒绝仍播放固定安全答复。"""
from __future__ import annotations

import struct
import wave

import pytest
from host.adapters.runtime import build_context
from host.ai import safety
from host.ai.providers import LlmReply, ToolCall
from host.audio.backend import Recording
from host.config.settings import load_settings
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import State, Trigger

from tests.ai_runtime import TracedTts


class ToolThenReply:
    def __init__(self, *calls, text="我来挥挥手。"):
        self.calls = calls
        self.text = text
        self.rounds = 0

    def complete(self, messages, tools=None, **kwargs):
        self.rounds += 1
        return LlmReply(tool_calls=self.calls) if self.rounds == 1 else LlmReply(text=self.text)


def request(context, monkeypatch, llm):
    context.engine.apply(Trigger.DIALOG_PRESS)
    context.engine.apply(Trigger.REC_STOP)
    context.router.llm = llm
    recording = Recording(struct.pack('<h', 4000) * 1600, 16000, duration_s=0.1)
    monkeypatch.setattr(context, '_await_recording', lambda generation: recording)
    generation = context.state_machine.generation
    context._handle_net_request(Command(CommandKind.NET_REQUEST, generation=generation))
    context.dispatch_bus()
    return generation


def commands(queue):
    items = []
    while (item := queue.get(timeout=0)) is not None:
        items.append(item)
    return items


def complete(context, kind=EventKind.PLAY_DONE):
    if kind is EventKind.PERFORM_DONE:
        context._end_play(context._active_play_seq)
    context.bus.publish(Event(kind, generation=context.state_machine.generation))
    context.dispatch_bus()


@pytest.fixture()
def local_audio(sim_context, tmp_path):
    root = tmp_path / 'audio'
    directory = root / 'story'
    directory.mkdir(parents=True)
    path = directory / 'hello.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack('<h', 2000) * 3200)
    sim_context.catalog.root = root
    return path


def test_ai_action_is_scheduled_after_safe_reply_finishes(sim_context, monkeypatch):
    context = sim_context
    llm = ToolThenReply(ToolCall('play_action', {'action_id': 'wave'}))
    generation = request(context, monkeypatch, llm)
    assert context.state_machine.state is State.SPEAKING
    assert not context.motion_busy, '还没说完回复就开始运动'
    context.bus.publish(Event(EventKind.PLAY_DONE, generation=generation))
    context.dispatch_bus()
    scheduled = [item for item in commands(context.work) if item.kind is CommandKind.PLAY_ACTION]
    assert len(scheduled) == 1, '工具声称已调度，但回复结束后运动队列没有动作'
    assert scheduled[0].payload['action_id'] == 'wave'
    assert scheduled[0].payload['action']['frames'][-1]['t_ms'] == 4000
    assert context.motion_busy and context.state_machine.state is State.PERFORMING


def test_blocked_output_still_delivers_fixed_refusal_clip(sim_context, monkeypatch):
    llm = ToolThenReply(text='你先把插座拆开，再摸里面的电线')
    llm.rounds = 1
    request(sim_context, monkeypatch, llm)
    spoken = [item for item in commands(sim_context.audio_queue)
              if item.kind is CommandKind.PLAY_AUDIO]
    assert len(spoken) == 1, '固定拒绝虽已合成，却在 net worker 被当失败丢弃'
    assert spoken[0].payload['reply_text'] == safety.GUIDE_ADULT_TEMPLATE
    assert sim_context.state_machine.state is State.SPEAKING


def test_no_installed_role_still_supports_plain_conversation(tmp_path, monkeypatch):
    context = build_context(load_settings({'DB_PATH': str(tmp_path / 'toy.db')}),
                            simulate=True, root=tmp_path)
    llm = ToolThenReply(text='你好呀，我们聊聊天。')
    llm.rounds = 1
    try:
        request(context, monkeypatch, llm)
        assert context.state_machine.state is State.SPEAKING, '无公仔时通用问答被会话守卫误拒'
        assert not context.motion_busy and not context.tool_performer.pending
    finally:
        context.close()


def test_local_content_reaches_audio_backend_after_reply(sim_context, monkeypatch, local_audio):
    context = sim_context
    llm = ToolThenReply(ToolCall('play_content', {'item_id': 'story:hello'}), text='我们听个故事。')
    request(context, monkeypatch, llm)
    first = commands(context.audio_queue)
    assert any(item.kind is CommandKind.PLAY_AUDIO and 'item_id' not in item.payload
               for item in first)
    complete(context)
    audio = [item for item in commands(context.audio_queue) if item.kind is CommandKind.PLAY_AUDIO]
    assert len(audio) == 1 and audio[0].payload['item_id'] == 'story:hello'
    assert audio[0].payload['clip'].data == local_audio.read_bytes()
    context._handle_audio_command(audio[0])
    context.dispatch_bus()
    assert context.player.backend.played == [0.2]
    assert context.state_machine.state is State.IDLE


def test_multiple_play_tools_are_serial_and_keep_request_order(
    sim_context, monkeypatch, local_audio,
):
    context = sim_context
    request(context, monkeypatch, ToolThenReply(
        ToolCall('play_content', {'item_id': 'story:hello'}),
        ToolCall('play_action', {'action_id': 'wave'}),
        ToolCall('play_action', {'action_id': 'nod'}), text='我们依次来。'))
    assert len(context.tool_performer.pending) == 3
    complete(context)
    assert context.state_machine.state is State.SPEAKING and not context.motion_busy
    complete(context)
    assert context.state_machine.state is State.PERFORMING and context.motion_busy
    assert context.action_progress['action_id'] == 'wave'
    complete(context, EventKind.PERFORM_DONE)
    assert context.action_progress['action_id'] == 'nod'
    complete(context, EventKind.PERFORM_DONE)
    assert context.state_machine.state is State.IDLE and not context.motion_busy


@pytest.mark.parametrize('operation', ['stop', 'dialog', 'perform', 'role', 'safety',
                                       'audio_fail', 'config', 'shutdown'])
def test_cancel_or_failure_discards_waiting_ai_action(sim_context, monkeypatch, operation):
    context = sim_context
    generation = request(context, monkeypatch,
                         ToolThenReply(ToolCall('play_action', {'action_id': 'wave'})))
    assert context.tool_performer.pending
    if operation == 'stop':
        context.stop_action()
    elif operation == 'dialog':
        context.press_button(1)
    elif operation == 'perform':
        context.press_button(2)
    elif operation == 'role':
        context.switch_active_role(2)
    elif operation == 'safety':
        context.trip_safety('INTERLOCK', '取消回归')
    elif operation == 'audio_fail':
        context.bus.publish(Event(EventKind.AUDIO_FAIL, payload={'phase': 'play'},
                                  generation=generation))
        context.dispatch_bus()
    elif operation == 'config':
        context.roles.active().config_version += '-changed'
    else:
        context.close()
    if operation != 'shutdown':
        context.bus.publish(Event(EventKind.PLAY_DONE, generation=generation))
        context.dispatch_bus()
    assert not context.tool_performer.pending and not context.motion_busy
    assert not any(item.kind is CommandKind.PLAY_ACTION for item in commands(context.work))
    if operation == 'safety':
        assert context.state_machine.state is State.FAULT_LOCKED


def test_output_review_rejection_drops_prepared_motion(sim_context, monkeypatch):
    context = sim_context
    llm = ToolThenReply(ToolCall('play_action', {'action_id': 'wave'}),
                       text='你先把插座拆开，再摸里面的电线')
    request(context, monkeypatch, llm)
    assert context.state_machine.state is State.SPEAKING
    assert not context.tool_performer.pending
    complete(context)
    assert context.state_machine.state is State.IDLE
    assert not any(item.kind is CommandKind.PLAY_ACTION for item in commands(context.work))


def test_cancel_before_provider_returns_prevents_tool_invocation(sim_context, monkeypatch):
    context = sim_context
    called = []
    context.router.registry.register('side_effect', '写操作回归', (),
                                     lambda args: called.append(1) or {})

    class LateTool(ToolThenReply):
        def complete(self, messages, tools=None, **kwargs):
            context.press_button(1)  # 模拟模型返回前，用户已经取消等待。
            return LlmReply(tool_calls=(ToolCall('side_effect', {}),))

    request(context, monkeypatch, LateTool())
    assert called == [] and context.state_machine.state is State.IDLE
    assert not context.tool_performer.pending


def test_repeated_call_id_does_not_duplicate_physical_task(sim_context, monkeypatch):
    call = ToolCall('play_action', {'action_id': 'wave'}, 'same-call')
    request(sim_context, monkeypatch, ToolThenReply(call, call))
    assert len(sim_context.tool_performer.pending) == 1


def test_reused_call_id_with_new_parameters_rejects_all_play_tasks(sim_context, monkeypatch):
    request(sim_context, monkeypatch, ToolThenReply(
        ToolCall('play_action', {'action_id': 'wave'}, 'same-call'),
        ToolCall('play_action', {'action_id': 'nod'}, 'same-call')))
    assert not sim_context.tool_performer.pending


def test_unfinished_tool_rounds_do_not_execute_partial_plan(sim_context, monkeypatch):
    class EndlessTool:
        def complete(self, messages, tools=None, **kwargs):
            return LlmReply(text='我来挥挥手。',
                            tool_calls=(ToolCall('play_action', {'action_id': 'wave'}),))

    request(sim_context, monkeypatch, EndlessTool())
    assert not sim_context.tool_performer.pending and not sim_context.motion_busy


@pytest.mark.parametrize('text', ['我已经挥手了。', '我已经开始播放了。', '动作已经执行完成。',
                                  'I have already played the story.'])
def test_pending_plan_does_not_speak_past_completion_claim(sim_context, monkeypatch, text):
    tts = TracedTts()
    sim_context.router.tts = tts
    request(sim_context, monkeypatch,
            ToolThenReply(ToolCall('play_action', {'action_id': 'wave'}), text=text))
    assert tts.texts and tts.texts[0] != text, '动作尚未执行，已经把完成声称送进 TTS'
    assert sim_context.tool_performer.pending, '时间表述修正不应丢掉合法播放请求'


@pytest.mark.parametrize('text', ['我接下来会挥挥手。', '故事里的小熊已经挥手了。',
                                  '小熊说：“我已经挥手了。”', '我们一会儿播放故事。'])
def test_future_or_fiction_text_is_preserved_with_pending_plan(sim_context, monkeypatch, text):
    tts = TracedTts()
    sim_context.router.tts = tts
    request(sim_context, monkeypatch,
            ToolThenReply(ToolCall('play_action', {'action_id': 'wave'}), text=text))
    assert tts.texts == [text] and sim_context.tool_performer.pending


def test_oversized_play_plan_is_rejected_as_a_whole(sim_context, monkeypatch):
    calls = [ToolCall('play_action', {'action_id': 'wave'}, f'call-{i}') for i in range(9)]
    request(sim_context, monkeypatch, ToolThenReply(*calls))
    assert not sim_context.tool_performer.pending and not sim_context.motion_busy


def test_old_audio_failure_does_not_cancel_current_reply_plan(sim_context, monkeypatch):
    generation = request(sim_context, monkeypatch,
                         ToolThenReply(ToolCall('play_action', {'action_id': 'wave'})))
    sim_context.bus.publish(Event(EventKind.AUDIO_FAIL, payload={'phase': 'play'},
                                 generation=generation - 1))
    sim_context.dispatch_bus()
    assert sim_context.tool_performer.pending, '旧音频失败清掉了新一轮计划'
    complete(sim_context)
    assert sim_context.motion_busy


def test_unsafe_mcu_state_clears_plan_without_waiting_for_role_change(sim_context, monkeypatch):
    generation = request(sim_context, monkeypatch,
                         ToolThenReply(ToolCall('play_action', {'action_id': 'wave'})))
    sim_context.bus.publish(Event(EventKind.MCU_STATE, payload={'state': (1, 0, 1)},
                                 generation=generation))
    sim_context.dispatch_bus()
    assert sim_context.state_machine.state is State.FAULT_LOCKED
    assert not sim_context.tool_performer.pending


@pytest.mark.parametrize('value', [32767, 0])
def test_clipped_or_empty_local_audio_never_reaches_play_queue(
    sim_context, monkeypatch, local_audio, value,
):
    with wave.open(str(local_audio), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack('<h', value) * (3200 if value else 0))
    request(sim_context, monkeypatch,
            ToolThenReply(ToolCall('play_content', {'item_id': 'story:hello'})))
    assert not sim_context.tool_performer.pending
    complete(sim_context)
    assert sim_context.state_machine.state is State.IDLE
