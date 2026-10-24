"""真实领域线程 + pyserial/TCP 模拟 MCU：语音工具从录音到执行/取消。"""
from __future__ import annotations

import json
import shutil
import threading
import time
from types import SimpleNamespace

import pytest
from host.adapters.runtime import build_context
from host.ai.providers import ToolCall
from host.config.settings import load_settings
from host.core.state_machine import State
from host.motion.serial_link import SerialLink
from sim.tcp_bridge import TcpBridge, make_controller

from tests.ai_runtime import ScriptedLlm, TracedAudio, TracedTts, wait_for, wav_bytes
from tests.support import TOY_ROOT


@pytest.fixture()
def rig(tmp_path):
    root = tmp_path / 'isolated'
    shutil.copytree(TOY_ROOT / 'content/roles', root / 'content/roles')
    audio_dir = root / 'content/audio/story'
    audio_dir.mkdir(parents=True)
    (audio_dir / 'hello.wav').write_bytes(wav_bytes())
    for name, axis in [('wave', 1), ('nod', 2)]:
        mid = [1500] * 4
        mid[axis] = 1540
        action = {'schema': 1, 'role_id': 1, 'name': name, 'frames': [
            {'t_ms': 0, 'us': [1500] * 4}, {'t_ms': 200, 'us': mid},
            {'t_ms': 400, 'us': [1500] * 4}]}
        (root / f'content/roles/1/actions/{name}.json').write_text(json.dumps(action),
                                                                 encoding='utf-8')
    controllers, wire = [], []

    def factory(clock):
        controller = make_controller(clock)
        controller.received_bytes = bytearray()
        receive = controller.receive

        def record(data):
            controller.received_bytes.extend(data)
            receive(data)

        controller.receive = record
        controllers.append(controller)
        return controller

    class Link(SerialLink):
        def open(self):
            super().open()
            wire.append(('open', time.monotonic(), ''))

        def write_line(self, line):
            super().write_line(line)
            wire.append(('write', time.monotonic(), line))

        def close(self):
            opened = self._ser is not None
            super().close()
            if opened:
                wire.append(('close', time.monotonic(), ''))

    bridge = TcpBridge(port=0, controller_factory=factory).start()
    link = Link(f'socket://127.0.0.1:{bridge.port}', read_timeout_s=0.02)
    context = build_context(load_settings({'DB_PATH': str(tmp_path / 'toy.db'),
                            'AI_PROVIDER': 'stub'}), root=root, link=link)
    context.motion.boot_wait_s = 0.3
    backend, llm, tts = TracedAudio(), ScriptedLlm(), TracedTts()
    context.player.backend.close()
    context.player.backend = context.recorder.backend = backend
    backend.open()
    context.router.llm, context.router.tts = llm, tts
    context.worker_running = True
    context.start_workers()
    stop = threading.Event()
    worker = threading.Thread(target=context.run_forever, args=(stop,), daemon=True)
    context.register_motion_thread(worker)
    worker.start()
    owned = list(context._domain_threads) + [worker]
    try:
        yield SimpleNamespace(context=context, backend=backend, llm=llm, tts=tts,
                              wire=wire, controllers=controllers)
    finally:
        llm.release.set()
        backend.reply_gate.set()
        stop.set()
        context.close()
        worker.join(2)
        bridge.stop()
        assert not any(thread.is_alive() for thread in owned)


def talk(rig):
    assert rig.context.press_button(1)['state'] == 'RECORDING'
    assert wait_for(rig.context._rec_ready.is_set, 1)
    assert rig.context.press_button(1)['state'] == 'THINKING'


def action_calls():
    return (ToolCall('play_content', {'item_id': 'story:hello'}),
            ToolCall('play_action', {'action_id': 'wave'}),
            ToolCall('play_action', {'action_id': 'nod'}))


def test_voice_to_content_then_two_real_serial_actions(rig):
    rig.llm.calls = action_calls()
    talk(rig)
    assert wait_for(lambda: len(rig.controllers) == 2
                    and rig.context.state_machine.state is State.IDLE, 7)
    starts = [item for item in rig.backend.trace if item[0] == 'start']
    ends = [item for item in rig.backend.trace if item[0] == 'end']
    opens = [item for item in rig.wire if item[0] == 'open']
    closes = [item for item in rig.wire if item[0] == 'close']
    assert len(starts) == len(ends) == len(opens) == len(closes) == 2
    assert starts[0][3] != wav_bytes() and starts[1][3] == wav_bytes()
    assert starts[1][2] >= ends[0][2] and opens[0][1] >= ends[1][2]
    assert opens[1][1] >= closes[0][1], '两条工具动作发生串口重叠'
    for controller in rig.controllers:
        lines = bytes(controller.received_bytes).splitlines()
        assert b'ARM 1' in lines and any(line.startswith(b'FRAME ') for line in lines)
        assert lines[-1] == b'STOP' and not controller.armed
    assert rig.context.last_result is not None and not rig.context.motion_busy
    assert rig.context.worker_faults == 0 and rig.context.worker_alive()


@pytest.mark.parametrize('operation', ['stop', 'role', 'safety', 'audio_failure'])
def test_interrupt_reply_drops_all_media_tasks(rig, operation):
    rig.llm.calls = action_calls()
    rig.backend.reply_gate.clear()
    rig.backend.fail_play = operation == 'audio_failure'
    talk(rig)
    assert rig.backend.started.wait(2)
    if operation == 'stop':
        rig.context.stop_action()
    elif operation == 'role':
        rig.context.switch_active_role(2)
    elif operation == 'safety':
        rig.context.trip_safety('INTERLOCK', '测试安全跳闸')
    rig.backend.reply_gate.set()
    target = State.FAULT_LOCKED if operation == 'safety' else State.IDLE
    assert wait_for(lambda: rig.context.state_machine.state is target)
    assert not rig.context.tool_performer.pending and not rig.wire
    assert rig.backend.plays == 1
    if operation == 'audio_failure':
        assert rig.context.worker_faults >= 1 and rig.context.worker_alive()


def test_stop_during_first_tool_action_cancels_remaining_actions(rig):
    rig.llm.calls = action_calls()[1:]
    talk(rig)
    assert wait_for(lambda: any(item[2].startswith('FRAME ') for item in rig.wire), 3)
    rig.context.stop_action()
    assert wait_for(lambda: any(item[0] == 'close' for item in rig.wire), 2)
    assert rig.context.state_machine.state is State.IDLE
    assert not rig.context.tool_performer.pending and not rig.context.motion_busy
    assert len(rig.controllers) == 1 and not rig.context.last_result
    assert bytes(rig.controllers[0].received_bytes).splitlines()[-1] == b'STOP'
    assert rig.context.worker_faults == 0


@pytest.mark.parametrize('operation', ['role', 'stop'])
def test_cancel_while_model_is_blocked_never_runs_old_tool(rig, monkeypatch, operation):
    rig.llm.calls = action_calls()[1:]
    rig.llm.release.clear()
    finished = threading.Event()
    original = rig.context._handle_net_request

    def observe(command):
        try:
            return original(command)
        finally:
            finished.set()

    monkeypatch.setattr(rig.context, '_handle_net_request', observe)
    talk(rig)
    assert rig.llm.entered.wait(1)
    if operation == 'role':
        rig.context.switch_active_role(2)
    else:
        rig.context.stop_action()
    rig.llm.release.set()
    assert finished.wait(2)
    assert wait_for(lambda: rig.context.bus.qsize() == 0)
    assert rig.context.state_machine.state is State.IDLE
    assert not rig.wire and not rig.backend.plays and not rig.context.tool_performer.pending
    assert not rig.tts.texts, '已取消的旧对话又发起了 TTS 请求'
    assert rig.context.worker_faults == 0, '用户取消被错误计为工作线程故障'
