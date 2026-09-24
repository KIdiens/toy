"""P0-1 装配的单元级护栏：SPEAKING 进入命令、迟到回包丢弃、命令路由分派。"""
from __future__ import annotations

from host.adapters.queues import CommandRouter
from host.core.bus import EventBus, WorkQueue
from host.core.engine import Engine
from host.core.events import Command, CommandKind, Event, EventKind
from host.core.state_machine import SessionStateMachine, State


def _engine(state: State, generation: int = 0) -> tuple[Engine, WorkQueue]:
    out: WorkQueue = WorkQueue()
    machine = SessionStateMachine(initial=state, generation=generation)
    return Engine(EventBus(), out, state_machine=machine), out


def test_entering_speaking_emits_play_audio_with_clip_payload():
    """AI_DONE → SPEAKING 必须下发 PLAY_AUDIO，且把 clip 原样透传给 audio worker。"""
    engine, out = _engine(State.THINKING)
    engine.submit(Event(EventKind.AI_DONE, payload={"clip": "CLIP", "reply_text": "你好"},
                        generation=0))
    assert engine.step(timeout=0.1) is True
    assert engine.state_machine.state is State.SPEAKING
    commands = out.drain()
    play = [c for c in commands if c.kind is CommandKind.PLAY_AUDIO]
    assert play, "进入 SPEAKING 必须下发 PLAY_AUDIO"
    assert play[0].payload["clip"] == "CLIP", "clip 必须透传到 PLAY_AUDIO 载荷"


def test_stale_ai_done_is_dropped_after_role_switch():
    """换角色（generation+1）后，上一代的 AI_DONE 不得把 THINKING 推进到 SPEAKING（AC-15）。"""
    engine, out = _engine(State.THINKING, generation=5)
    engine.submit(Event(EventKind.AI_DONE, payload={"clip": "OLD"}, generation=4))
    engine.step(timeout=0.1)
    assert engine.state_machine.state is State.THINKING, "迟到回包不得推进状态"
    assert out.empty()

    engine.submit(Event(EventKind.AI_DONE, payload={"clip": "NEW"}, generation=5))
    engine.step(timeout=0.1)
    assert engine.state_machine.state is State.SPEAKING, "同代回包应正常推进"


def test_command_router_dispatches_by_kind():
    router = CommandRouter()
    router.put(Command(CommandKind.NET_REQUEST))
    router.put(Command(CommandKind.RECORD_START))
    router.put(Command(CommandKind.PLAY_AUDIO))
    router.put(Command(CommandKind.STOP_AUDIO))
    router.put(Command(CommandKind.PLAY_ACTION))
    router.put(Command(CommandKind.NOTIFY))
    assert router.net.qsize() == 1, "NET_REQUEST 必须进 net 队列"
    assert router.audio.qsize() == 3, "RECORD_START/PLAY_AUDIO/STOP_AUDIO 必须进 audio 队列"
    assert router.motion.qsize() == 2, "PLAY_ACTION/NOTIFY 归 motion 队列"
