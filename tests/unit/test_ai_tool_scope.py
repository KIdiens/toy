"""云阶段取消、并发隔离及待执行效果不泄漏到下一轮。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from host.ai.providers import AudioClip, LlmReply, ToolCall
from host.ai.router import AiRouter
from host.ai.tool_scope import defer_effect, request_metadata, request_scope
from host.ai.tools import ToolRegistry
from host.audio.catalog import ContentCatalog
from host.core.errors import AiCancelled, ToolValidationError
from host.tools import content as content_tool

from tests.ai_runtime import wav_bytes


@pytest.mark.parametrize('stage,expected', [
    ('before', []), ('asr', ['asr']), ('llm1', ['asr', 'llm1']),
    ('tool', ['asr', 'llm1', 'tool']),
    ('llm2', ['asr', 'llm1', 'tool', 'llm2']),
    ('tts', ['asr', 'llm1', 'tool', 'llm2', 'tts']),
])
def test_cancel_at_provider_boundary_stops_next_stage(stage, expected):
    trace, cancelled = [], [stage == 'before']

    def touch(name):
        trace.append(name)
        if name == stage:
            cancelled[0] = True

    class Asr:
        def transcribe(self, wav, lang=None):
            touch('asr')
            return '你好呀'

    class Llm:
        calls = 0

        def complete(self, messages, tools=None):
            self.calls += 1
            touch(f'llm{self.calls}')
            return (LlmReply(tool_calls=(ToolCall('task', {}),)) if self.calls == 1
                    else LlmReply(text='我们依次来。'))

    class Tts:
        def synthesize(self, text, voice, lang=None):
            touch('tts')
            return AudioClip(b'RIFF____WAVE', 16000)

    def task(args):
        touch('tool')
        defer_effect({'kind': 'test'})
        return {'ok': True}

    registry = ToolRegistry()
    registry.register('task', '测试任务', (), task)
    router = AiRouter(Asr(), Llm(), Tts(), registry)
    with (request_scope(lambda: not cancelled[0], {'round': 'cancelled'}),
          pytest.raises(AiCancelled)):
        router.respond_audio(b'wav')
    assert trace == expected and request_metadata() is None


def test_concurrent_rounds_keep_effects_and_cancellation_separate():
    barrier = threading.Barrier(2)
    flags = {'current': False, 'cancelled': False}

    class Llm:
        def complete(self, messages, tools=None):
            if any(message.role == 'tool' for message in messages):
                return LlmReply(text='我来挥挥手。')
            barrier.wait(2)
            tag = request_metadata()['round']
            if tag == 'cancelled':
                flags[tag] = True
            return LlmReply(tool_calls=(ToolCall('task', {}),))

    class Tts:
        def synthesize(self, text, voice, lang=None):
            return AudioClip(b'RIFF____WAVE', 16000)

    def task(args):
        defer_effect({'round': request_metadata()['round']})
        return {'ok': True}

    registry = ToolRegistry()
    registry.register('task', '测试任务', (), task)
    router = AiRouter(None, Llm(), Tts(), registry)

    def run(tag):
        with request_scope(lambda: not flags[tag], {'round': tag}):
            try:
                return router.respond_text('你好呀')
            except AiCancelled:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        current = pool.submit(run, 'current')
        cancelled = pool.submit(run, 'cancelled')
        result, discarded = current.result(3), cancelled.result(3)
    assert result.performances == ({'round': 'current'},)
    assert discarded is None and request_metadata() is None


@pytest.mark.parametrize('name,args', [('play_action', {'action_id': 'wave'}),
                                      ('play_content', {'item_id': 'story:hello'})])
def test_unwired_play_tool_never_reports_success(tmp_path, name, args):
    directory = tmp_path / 'story'
    directory.mkdir()
    (directory / 'hello.wav').write_bytes(wav_bytes())
    registry = ToolRegistry()
    content_tool.register(registry, catalog=ContentCatalog(tmp_path), action_ids=lambda: ['wave'])
    with pytest.raises(ToolValidationError, match='未装配'):
        registry.invoke(name, args)
