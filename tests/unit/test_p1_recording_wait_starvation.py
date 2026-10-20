"""P1：录音等待被新一轮录音饿死 —— 旧 NET_REQUEST 空等满 ``REPLY_WAIT_S``。

## 缺陷机制（复现见 ``data/g10/probe_starvation.py``）

``_await_recording`` 只在**进入时**检查一次 ``_rec_started_gen``，等待循环里只认
``_rec_ready && _last_recording_gen == generation``。旧请求进入等待后用户又按了一次
开始录音 → ``_record_once`` 清掉 ``_rec_ready`` 并把 ``_rec_started_gen`` 改成新代数
→ 旧请求的条件永远不成立 → 空等 16 秒。net 是单消费者，排在后面的所有
NET_REQUEST 一起被堵住。

## 为什么必须用「带阻塞的录音替身」

无硬件档位下 ``NullAudioBackend.record()`` **立即返回**：旧请求进入等待前本轮录音
就已就绪，实测 5/5 次首请求等待 0.00s，复现不出来。真机声卡录音是阻塞的，所以本
文件的集成用例一律注入 ``_BlockingRecorder``。这是**真机路径**的缺陷，不是仿真路径
的缺陷 —— 这个界必须说清楚，否则会被误读成「仿真下也坏了」。

## 三类用例的分工

- 纯等待循环（假时钟）：判别力最强、确定性 100%，覆盖「代数复查」「正常单轮」
  「从未开始」「陈旧残留」「超时上限」五条边界；
- 真实线程 + 阻塞录音替身：证明整条链路（audio 线程录、net 线程等）真的不被饿死；
- 正常单轮对照：证明修复不是「一刀切成不等待」。
"""
from __future__ import annotations

import threading
import time
import types

import pytest
from host.adapters.runtime import build_context
from host.adapters.workers import RECORD_POLL_S, REPLY_WAIT_S
from host.config.settings import load_settings
from host.storage.database import Database

from tests.support import TOY_ROOT

GENERATION = 6
NEXT_GENERATION = 7
WAV_BYTES = b"RIFF" + b"\x00" * 64


# ---------------------------------------------------------------- 装置
class _FakeClock:
    """只按 ``sleep`` 前进的假时钟；``on_sleep`` 用于在等待中途制造外部事件。

    不真睡 —— 判别力靠断言「走了多少毫秒」，而不是靠墙钟。
    """

    def __init__(self, on_sleep=None) -> None:
        self.t = 0
        self.sleeps = 0
        self._on_sleep = on_sleep

    def now_ms(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.t += max(1, int(round(seconds * 1000)))
        if self._on_sleep is not None:
            self._on_sleep(self)


class _BlockingRecorder:
    """真机路径替身：``record`` 阻塞 ``delay`` 秒后才返回（真机必然如此）。"""

    policy = types.SimpleNamespace(max_seconds=15.0)

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def record(self, seconds):
        self.calls += 1
        time.sleep(self.delay)
        return types.SimpleNamespace(data=WAV_BYTES, duration_s=self.delay)


class _StubRouter:
    """记录拿到的 wav 字节数；不调真模型（本文件不测 AI）。"""

    def __init__(self) -> None:
        self.wav_sizes: list[int] = []

    def respond_audio(self, wav):
        self.wav_sizes.append(len(wav))
        return types.SimpleNamespace(
            blocked=True, reason="stub", clip=None, reply_text="", user_text=""
        )


def _context(tmp_path, name: str = "p1.sqlite3"):
    settings = load_settings({"DB_PATH": str(tmp_path / name)})
    return build_context(settings, simulate=True,
                         db=Database(tmp_path / name), root=TOY_ROOT)


def _set_started(context, generation: int) -> None:
    with context._rec_lock:
        context._rec_started_gen = generation


def _publish_ready(context, generation: int, recording) -> None:
    with context._rec_lock:
        context._last_recording = recording
        context._last_recording_gen = generation
        context._rec_ready.set()


def _bare(tmp_path, name: str = "p1.sqlite3"):
    """不启动任何线程的上下文 —— 纯等待循环用例用。"""
    return _context(tmp_path, name)


class _Running:
    """启动 net/audio/tick + motion worker；退出时一定 join，绝不留线程。"""

    def __init__(self, context) -> None:
        self.context = context
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        self.context.worker_running = True
        self.context.start_workers()
        self.thread = threading.Thread(
            target=self.context.run_forever, args=(self.stop,), daemon=True,
            name="p1-motion")
        self.context.register_motion_thread(self.thread)
        self.thread.start()
        return self.context

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.context.close()
        if self.thread is not None:
            self.thread.join(timeout=3.0)


def _timed_await(context):
    """包一层计时，返回 (waits, restore)。"""
    waits: list[tuple[int, float]] = []
    original = context._await_recording

    def timed(generation: int):
        started = time.monotonic()
        result = original(generation)
        waits.append((generation, time.monotonic() - started))
        return result

    context._await_recording = timed  # type: ignore[method-assign]
    return waits, lambda: setattr(context, "_await_recording", original)


def _wait_for(cond, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# =====================================================================
# 1. 等待循环：代数被改写必须立即返回，不许空等到 16s
# =====================================================================
def test_newer_recording_round_releases_old_waiter_immediately(tmp_path) -> None:
    """**核心判别力用例**：等待中途出现新一轮录音（代数变了）→ 立即返回 None。

    回退 ``_await_recording`` 里的代数复查，本用例走的毫秒数会变成 16000，
    断言随之转红（这正是反向验证的那一条）。
    """
    context = _bare(tmp_path)
    try:
        _set_started(context, GENERATION)
        # 第 3 次轮询时，audio 线程开始了新一轮录音 → 代数被改写
        clock = _FakeClock(
            on_sleep=lambda c: _set_started(context, NEXT_GENERATION) if c.sleeps == 3 else None
        )
        context.clock = clock

        result = context._await_recording(GENERATION)

        assert result is None, "旧代数不该拿到任何录音"
        assert clock.now_ms() <= 5 * int(RECORD_POLL_S * 1000), (
            f"旧请求空等了 {clock.now_ms()}ms —— 代数已变，本轮不可能再就绪，应立即返回"
        )
        assert clock.now_ms() < int(REPLY_WAIT_S * 1000) / 4
    finally:
        context.close()


def test_single_round_waits_until_recording_is_ready(tmp_path) -> None:
    """**不许一刀切成 0**：正常单轮要真的等到录音就绪才返回，且拿到的就是本轮数据。"""
    context = _bare(tmp_path)
    try:
        recording = object()
        _set_started(context, GENERATION)
        # 第 10 次轮询（200ms）时录音完成 —— 模拟「说完再按结束」
        clock = _FakeClock(
            on_sleep=lambda c: (_publish_ready(context, GENERATION, recording)
                                if c.sleeps == 10 else None)
        )
        context.clock = clock

        assert context._await_recording(GENERATION) is recording
        assert clock.sleeps == 10, "没等到录音就绪就返回了 —— 等待被打断"
        assert clock.now_ms() == 10 * int(RECORD_POLL_S * 1000)
    finally:
        context.close()


def test_round_that_never_started_returns_without_sleeping(tmp_path) -> None:
    """本轮从未开始录音（进入时代数就不等）→ 一次都不睡，立即 None。"""
    context = _bare(tmp_path)
    try:
        _set_started(context, GENERATION + 5)
        clock = _FakeClock()
        context.clock = clock

        assert context._await_recording(GENERATION) is None
        assert clock.sleeps == 0 and clock.now_ms() == 0
    finally:
        context.close()


def test_stale_recording_from_previous_round_is_never_taken(tmp_path) -> None:
    """上一轮残留（``_rec_ready`` 已 set、但属于旧代数）不得被本轮误取。"""
    context = _bare(tmp_path)
    try:
        stale = object()
        _set_started(context, GENERATION)
        _publish_ready(context, GENERATION - 1, stale)   # 残留：ready 但代数不匹配
        clock = _FakeClock(
            on_sleep=lambda c: _set_started(context, NEXT_GENERATION) if c.sleeps == 4 else None
        )
        context.clock = clock

        result = context._await_recording(GENERATION)
        assert result is None
        assert result is not stale, "取到了上一轮的残留录音"
        assert clock.now_ms() <= 5 * int(RECORD_POLL_S * 1000)
    finally:
        context.close()


def test_timeout_ceiling_survives_the_fix(tmp_path) -> None:
    """代数一直不变、录音一直不就绪 → 仍在 ``REPLY_WAIT_S`` 处收口，不是无限等。"""
    context = _bare(tmp_path)
    try:
        _set_started(context, GENERATION)
        clock = _FakeClock()
        context.clock = clock

        assert context._await_recording(GENERATION) is None
        assert clock.now_ms() == int(REPLY_WAIT_S * 1000)
        assert clock.sleeps == int(REPLY_WAIT_S * 1000) // int(RECORD_POLL_S * 1000)
    finally:
        context.close()


# =====================================================================
# 2. 真实线程 + 阻塞录音替身：连点开始不得饿死旧请求
# =====================================================================
def test_rapid_restart_does_not_starve_net_thread(tmp_path) -> None:
    """4 次连点（30ms 间隔）+ 阻塞录音 → 旧请求的等待时长必须远小于 ``REPLY_WAIT_S``。

    ``_BlockingRecorder(0.3)``：若用 ``NullAudioBackend``（record 立即返回），
    旧请求进入等待时本轮录音已就绪，本用例测不出缺陷（实测 5/5 次 0.00s）。
    """
    context = _context(tmp_path, "storm.sqlite3")
    context.router = _StubRouter()
    context.recorder = _BlockingRecorder(0.3)
    waits, restore = _timed_await(context)
    try:
        with _Running(context):
            states = [context.press_button(1)["state"]]
            for _ in range(3):
                time.sleep(0.03)
                states.append(context.press_button(1)["state"])
            assert states[0] == "RECORDING" and "THINKING" in states, states
            # 上限刻意放到 REPLY_WAIT_S 之上：修复前这里要等满 16s 才拿到数据，
            # 让它自己走完，才能把「等了 16.0s」这个数字写进失败信息。
            assert _wait_for(lambda: waits, REPLY_WAIT_S + 4.0), (
                "请求始终没返回（被饿死），用例失去判别力"
            )
            assert context.net_queue.qsize() == 0, "net 队列仍有积压"
        worst = max(elapsed for _gen, elapsed in waits)
        assert len(waits) >= 1
        assert worst < REPLY_WAIT_S / 8, (
            f"旧请求最长等了 {worst:.2f}s —— 逼近 REPLY_WAIT_S={REPLY_WAIT_S}s，"
            f"net 单消费者被堵死；各次等待: {[(g, round(d, 2)) for g, d in waits]}"
        )
    finally:
        restore()
        context.close()


def test_normal_single_round_still_gets_its_recording(tmp_path) -> None:
    """对照：按一下开始、说 0.2s、按一下结束 —— 请求必须真的拿到本轮录音数据。

    这条是「修复不是一刀切成不等待」的链路级证据：只有它绿，上面那条快返回才算
    「修对了」，而不是「反正都返回 None」。
    """
    context = _context(tmp_path, "single.sqlite3")
    router = _StubRouter()
    context.router = router
    context.recorder = _BlockingRecorder(0.15)
    waits, restore = _timed_await(context)
    try:
        with _Running(context):
            assert context.press_button(1)["state"] == "RECORDING"
            time.sleep(0.2)                      # 「说 0.2 秒」
            assert context.press_button(1)["state"] == "THINKING"
            assert _wait_for(lambda: waits, 6.0), "请求没有返回"
        generation, elapsed = waits[0]
        assert elapsed < REPLY_WAIT_S / 4, f"正常单轮等了 {elapsed:.2f}s"
        assert router.wav_sizes == [len(WAV_BYTES)], (
            f"路由器拿到的 wav 长度 {router.wav_sizes} —— "
            f"没拿到本轮录音（应为 {len(WAV_BYTES)} 字节）"
        )
    finally:
        restore()
        context.close()


def test_request_really_waits_when_recording_is_still_in_flight(tmp_path) -> None:
    """请求**真正进入等待循环**的单轮：录音还没结束就按了结束，仍必须拿到数据，且等了。

    上一条的录音在按键前就完成了（``ready`` 早已 set），它证明不了「等待没被砍掉」。
    这一条把「按结束」压在录音阻塞期内（录音 0.5s、0.05s 就按结束），请求必须在循环
    里等约 0.45s 才拿到数据 —— 既是「不许一刀切成 0」的链路证据，也是「早退只由代数
    变化触发」的证据：若早退条件写宽松（例如「本轮还没 ready 就返回 None」），
    ``router.wav_sizes`` 会变成 ``[0]``，本条立刻转红。
    """
    context = _context(tmp_path, "inflight.sqlite3")
    router = _StubRouter()
    context.router = router
    context.recorder = _BlockingRecorder(0.5)
    waits, restore = _timed_await(context)
    try:
        with _Running(context):
            assert context.press_button(1)["state"] == "RECORDING"
            time.sleep(0.05)                     # 录音还没结束就按结束
            assert context.press_button(1)["state"] == "THINKING"
            assert _wait_for(lambda: waits, 6.0), "请求没有返回"
        _generation, elapsed = waits[0]
        assert router.wav_sizes == [len(WAV_BYTES)], (
            f"录音在途时按结束，请求拿到 {router.wav_sizes} 字节 —— 等待被提前放弃"
        )
        assert elapsed >= 0.2, (
            f"只等了 {elapsed:.2f}s —— 请求没有真的等待在途录音（录音需 0.5s）"
        )
    finally:
        restore()
        context.close()


@pytest.mark.parametrize("delay", [0.05, 0.2])
def test_recording_is_delivered_for_each_single_round(tmp_path, delay: float) -> None:
    """两档录音耗时都要拿到数据（不依赖某个特定阻塞时长的巧合）。"""
    context = _context(tmp_path, f"single_{delay}.sqlite3")
    router = _StubRouter()
    context.router = router
    context.recorder = _BlockingRecorder(delay)
    waits, restore = _timed_await(context)
    try:
        with _Running(context):
            context.press_button(1)
            time.sleep(delay + 0.05)
            context.press_button(1)
            assert _wait_for(lambda: waits, 6.0), "请求没有返回"
        assert router.wav_sizes == [len(WAV_BYTES)], router.wav_sizes
    finally:
        restore()
        context.close()
