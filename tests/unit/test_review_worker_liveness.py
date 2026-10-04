"""第二批整改 · 第 2 组：领域线程静默死亡与存活探针盲区。

本文件是**先写的复现**：在修复前跑必须全红，修复后必须全绿。

缺陷
----
D1  `_net_loop` 缺外层 try/except：`net_queue.get()` 或任何未被 `_handle_net_request`
    捕获的异常（如 `self.state_machine` 已被替换、事件总线被关闭抛错）会**直接杀死
    net 线程**。net 线程一死，NET_REQUEST 再无人消费，按对话键永久停在 THINKING
    （这正是 P0-1 修复的那个症状，兜底缺失让它可以复发）。
D2  `worker_alive()` 只看 `worker_heartbeat_ms`，而心跳**只由 motion worker 写**
    （`execution.run_forever`）。net/audio/tick 三个领域线程全死时，
    `/status` 仍返回 `worker_alive: true` —— 存活探针对领域线程完全失明。
"""
from __future__ import annotations

import threading

import pytest
from host.core.events import CommandKind


@pytest.fixture()
def ctx(sim_context):
    return sim_context


# --------------------------------------------------------------------------- D1
def test_net_worker_survives_queue_failure(ctx):
    """队列本身抛异常时 net 线程必须活着（否则对话闭环永久断链）。"""
    stop = threading.Event()
    boom = RuntimeError("队列底层已关闭")

    class ExplodingQueue:
        def get(self, timeout=None):
            raise boom

    ctx.net_queue = ExplodingQueue()
    thread = threading.Thread(target=ctx._net_loop, args=(stop,), daemon=True)
    thread.start()
    try:
        # 首次 get 就会抛；线程必须在若干轮后仍然存活
        for _ in range(50):
            thread.join(timeout=0.02)
            if not thread.is_alive():
                break
        assert thread.is_alive(), (
            "net 线程因 net_queue.get() 抛异常而静默死亡 —— "
            "此后所有 NET_REQUEST 无人消费，按对话键会永久停在 THINKING"
        )
    finally:
        stop.set()
        thread.join(timeout=2.0)


def test_net_loop_records_faults(ctx, monkeypatch):
    """兜底捕获必须计入 worker_faults（可见），不能静默吞掉。

    用「确定会抛」的 _respond，而不是真实推理路径——后者耗时不确定，
    断言会变成竞态而不是行为验证。
    """
    def boom(_command):
        raise RuntimeError("推理炸了")

    monkeypatch.setattr(ctx, "_respond", boom)
    before = ctx.worker_faults
    ctx.net_queue.put(_net_request(ctx))
    stop = threading.Event()
    thread = threading.Thread(target=ctx._net_loop, args=(stop,), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if ctx.worker_faults > before:
                break
            stop.wait(0.01)
    finally:
        stop.set()
        thread.join(timeout=2.0)
    assert ctx.worker_faults > before, "异常未被计入 worker_faults，等于静默失败"


def test_audio_worker_survives_queue_failure(ctx):
    """audio 线程同纪律：取件抛异常也不得静默死亡（否则录音/播放链断）。"""
    stop = threading.Event()

    class ExplodingQueue:
        def get(self, timeout=None):
            raise RuntimeError("队列底层已关闭")

    ctx.audio_queue = ExplodingQueue()
    thread = threading.Thread(target=ctx._audio_loop, args=(stop,), daemon=True)
    thread.start()
    try:
        for _ in range(50):
            thread.join(timeout=0.02)
            if not thread.is_alive():
                break
        assert thread.is_alive(), "audio 线程因 audio_queue.get() 抛异常而静默死亡"
    finally:
        stop.set()
        thread.join(timeout=2.0)


def test_net_worker_survives_dispatcher_blowup(ctx, monkeypatch):
    """状态机不可用这类"取命令之后"的异常也不得杀死线程。"""

    class Boom:
        @property
        def generation(self):
            raise RuntimeError("宿主演化中")

    generation = ctx.state_machine.generation  # patch 之前取，之后 state_machine 不可读
    monkeypatch.setattr(ctx, "state_machine", Boom())
    stop = threading.Event()
    ctx.net_queue.put(_net_request_with(generation))
    thread = threading.Thread(target=ctx._net_loop, args=(stop,), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            thread.join(timeout=0.02)
            if not thread.is_alive():
                break
        assert thread.is_alive(), (
            "net 线程在读取 state_machine.generation 时抛异常即静默死亡 —— "
            "此后所有 NET_REQUEST 无人消费"
        )
    finally:
        stop.set()
        thread.join(timeout=2.0)


# --------------------------------------------------------------------------- D2
def test_worker_alive_covers_domain_threads(ctx):
    """领域线程全死时 worker_alive 必须为 False（探针不能对领域线程失明）。"""
    ctx.start_workers()
    assert ctx.worker_alive() is True, "线程健在时探针必须报活"

    # 模拟领域线程意外死亡：清空句柄列表并让真实线程退出
    ctx._worker_stop.set()
    for thread in list(ctx._domain_threads):
        thread.join(timeout=2.0)
    assert ctx.worker_alive() is False, (
        "所有领域线程已退出，worker_alive 仍为 True —— "
        "motion 心跳掩盖了领域线程全死，/status 会给出假的健康信号"
    )


def test_worker_alive_false_when_a_single_domain_thread_dies(ctx):
    """只死一个领域线程也必须被观测到（不能只做全量判断）。"""
    ctx.start_workers()
    # 只让 tick 线程退出，net/audio 仍健在
    for thread in list(ctx._domain_threads):
        if thread.name == "host-tick":
            # 单线程无法只停一个；改为把句柄替换成一个已死线程
            dead = threading.Thread(target=lambda: None, daemon=True)
            dead.start()
            dead.join(timeout=1.0)
            ctx._domain_threads = [t for t in ctx._domain_threads if t is not thread] + [dead]
    assert ctx.worker_alive() is False, (
        "tick 线程已死但 worker_alive 仍为 True —— 单线程死亡被漏报"
    )
    ctx.stop_workers()


def test_worker_alive_false_before_start(ctx):
    """从未启动过 worker 时不得报活。"""
    assert ctx.worker_alive() is False


# --------------------------------------------------------------------------- helpers
def _net_request(ctx) -> object:
    from host.core.events import Command

    return Command(
        CommandKind.NET_REQUEST,
        payload={},
        generation=ctx.state_machine.generation,
    )


def _net_request_with(generation: int) -> object:
    from host.core.events import Command

    return Command(CommandKind.NET_REQUEST, payload={}, generation=generation)
