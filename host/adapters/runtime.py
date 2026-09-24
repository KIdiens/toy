"""运行期装配：读配置 → 建总线/核心/仓储/端口 → 返回可用的 ``AppContext``。

- 这是**组装**，不含业务逻辑；``main.py`` 只调用本函数再挂 web。
- 无硬件档位（``--simulate``）：MCU 用进程内模拟器 + 注入时钟；无 Key 时 AI 自动落桩。
- 依赖方向：adapters 向下依赖 core/ai/audio/roles/storage/config；``sim`` 仅在此按需延迟 import。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from host.adapters.ports import AppContext
from host.adapters.queues import CommandRouter
from host.ai.router import AiRouter
from host.ai.stub_provider import build_stub_triad
from host.ai.tools import ToolRegistry
from host.audio.catalog import ContentCatalog
from host.audio.null_backend import NullAudioBackend
from host.audio.player import Player
from host.audio.recorder import Recorder
from host.core.bus import EventBus, WorkQueue
from host.core.clock import RealClock
from host.core.engine import Engine
from host.core.events import Command
from host.core.state_machine import SessionStateMachine
from host.motion.client import MotionClient
from host.motion.serial_link import SerialLink
from host.motion.sim_link import SimLink
from host.roles.registry import RoleRegistry
from host.storage.database import Database
from host.tools import content as content_tool
from host.tools import reminders as reminder_tool
from host.tools import weather as weather_tool

REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_PORT_TOKEN = "sim://"


def _build_ai(settings, registry: ToolRegistry, memory_provider, logger) -> AiRouter:
    """按 ``AI_PROVIDER`` 选真/桩；Qwen 失败一律回落桩（AC-17 无致命错误）。"""
    if settings.ai.is_stub:
        asr, llm, tts = build_stub_triad()
    else:
        try:
            from host.ai.qwen_provider import build_from_env  # noqa: PLC0415

            asr, llm, tts = build_from_env()
        except Exception as exc:  # 缺 Key / 依赖 / 网络配置 → 落桩而非崩溃
            logger.warning("Qwen 提供方不可用，回落确定性桩后端: %s", exc)
            asr, llm, tts = build_stub_triad()
    return AiRouter(asr, llm, tts, registry, memory_provider=memory_provider, logger=logger)


def _make_sim(clock):
    from sim.controller import McController  # noqa: PLC0415  仅 SIM 档位需要
    from sim.devices import Buttons, Dock, Estop, RoleResistor  # noqa: PLC0415

    return McController(
        clock,
        pwm_ready=True,
        dock=Dock(engaged=True),
        estop=Estop(pressed=False),
        role=RoleResistor(role=1),
        buttons=Buttons(),
    )


def _make_link(settings, *, simulate: bool, clock, sim, link):
    if link is not None:
        return link, sim
    if simulate or settings.motion.port == SIM_PORT_TOKEN:
        controller = sim if sim is not None else _make_sim(clock)
        return SimLink(controller, clock=clock), controller
    return SerialLink(
        settings.motion.port,
        baudrate=settings.motion.baudrate,
        read_timeout_s=settings.motion.read_timeout_s,
        write_timeout_s=settings.motion.write_timeout_s,
    ), sim


def build_context(
    settings,
    *,
    simulate: bool = False,
    clock: Any = None,
    db: Database | None = None,
    sim: Any = None,
    link: Any = None,
    root: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> AppContext:
    """装配一个可运行上下文。测试可注入 ``clock``/``sim``/``link``/``db``。"""
    base = Path(root) if root is not None else REPO_ROOT
    log = logger or logging.getLogger("host")
    clock = clock or RealClock()

    state_machine = SessionStateMachine(logger=log)
    bus = EventBus()
    # 一条队列拆成三路：motion 归 host-worker，net/audio 各有独立消费者线程（P0-1）。
    router_out = CommandRouter()
    work: WorkQueue[Command] = router_out.motion
    engine = Engine(bus, router_out, state_machine=state_machine, clock=clock, logger=log)

    database = db if db is not None else Database(settings.storage.path)
    database.initialize()

    roles = RoleRegistry(base / "content" / "roles", state_machine=state_machine, logger=log)
    roles.load_all()
    if roles.list() and roles.active_role_id is None:
        roles.set_active(roles.list()[0].role_id, bump=False)

    catalog = ContentCatalog(base / "content" / "audio")
    backend = NullAudioBackend()
    player = Player(backend, clock=clock)
    recorder = Recorder(backend, player=player)

    registry = ToolRegistry()
    weather_tool.register(registry, weather_tool.StubWeatherClient())
    reminder_tool.register(registry, database, now_provider=clock.now_iso)
    content_tool.register(registry, catalog=catalog, action_ids=lambda: roles.actions_for())

    motion_link, controller = _make_link(
        settings, simulate=simulate, clock=clock, sim=sim, link=link
    )
    motion = MotionClient(motion_link, clock, boot_wait_s=settings.motion.boot_wait_s, logger=log)

    context = AppContext(
        settings=settings,
        clock=clock,
        bus=bus,
        work=work,
        engine=engine,
        state_machine=state_machine,
        db=database,
        roles=roles,
        catalog=catalog,
        router=None,
        player=player,
        recorder=recorder,
        scheduler=reminder_tool.ReminderScheduler(database, now_provider=clock.now_iso),
        motion=motion,
        sim=controller,
        simulate=bool(simulate),
        network_online=not settings.ai.is_stub,
        log=log,
        net_queue=router_out.net,
        audio_queue=router_out.audio,
        _router=router_out,
    )
    context.router = _build_ai(
        settings, registry, lambda: context.memory_context(context.ensure_profile()), log
    )
    context.boot()  # 上电序列：MCU 上电 → BOOT_OK → IDLE（装配的一部分，非业务逻辑）
    return context
