"""复验 P1 返工：``worker_alive`` 在 motion 播放期间误报 False（假故障）。

## 缺陷机理

第 2 组整改给 ``worker_alive`` 加了心跳维度（``ports.py``），
但心跳只在 ``execution.run_forever`` 的 ``while`` 循环**开头**写
（``execution.py:164``），紧接着就进入 ``pump_once()`` -> ``play_action()``。
后者**同步阻塞整个动作时长**（``client.py`` 轮询到末帧保持 150ms 才返回）。

于是整个播放期间心跳停止更新，超过 ``WORKER_LIVENESS_MS``（5000ms）后
``worker_alive()`` 返回 False —— 而此时**所有线程都健在**。

生产可达性已实测确认：``content/roles/1/actions/wave.json`` 时间轴 4000ms
+ ``HOLD_FINAL_S`` 150ms + 轮询/通信开销，单次播放极易越过 5 秒。
``TAIL_TIMEOUT_S`` 只管末帧之后的超时，**不限制时间轴本身**，故阻塞时长无固定上界。

探针的语义是「线程是否健在」，返回一个谎报就是缺陷 —— 而且这正是本批次
自己新引入的探针，属于自造缺陷必须自己修掉。

## 修法

不用「提高阈值」治标（阻塞时长无上界，阈值定多少都可能再被越过）。
改为**登记 motion 线程引用，探针逐个查 ``is_alive()``** —— 与领域线程同源，
是权威依据；心跳退为「线程尚未登记」时的兜底。
"""
from __future__ import annotations

import threading
import time

import pytest
from host.adapters.ports import WORKER_LIVENESS_MS


class _Ctx:
    """最小复刻存活探针所需的字段（不拉起整个 AppContext）。"""

    def __init__(self, *, domain_alive=True, motion_thread=None, heartbeat_ms=None):
        self._domain_ok = domain_alive
        self._domain_threads = [type("T", (), {"is_alive": lambda s: domain_alive})()]
        self._motion_thread = motion_thread
        self.worker_heartbeat_ms = heartbeat_ms
        # 探针要读 clock.now_ms() 算心跳年龄；这里只需要一个真实时钟
        self.clock = type("C", (), {"now_ms": staticmethod(lambda: int(time.time() * 1000))})()


class _CtxWithProbe(_Ctx):
    """绑定真实的 worker_alive 实现（从 AppContext 借方法，避免复制实现）。"""

    @property
    def worker_alive(self):
        from host.adapters.ports import AppContext

        return AppContext.worker_alive.__get__(self, type(self))


def test_motion_thread_reference_is_registered():
    """context 上必须有 motion 线程的登记位，否则探针根本查不到它。"""
    ctx = _CtxWithProbe()
    assert hasattr(ctx, "_motion_thread"), "缺少 motion 线程登记字段"


def test_heartbeat_never_written_does_not_report_dead_when_threads_registered():
    """线程全部登记且健在、心跳从未写过 -> 必须报活（不靠心跳猜）。"""
    thread = type("T", (), {"is_alive": lambda s: True})()
    ctx = _CtxWithProbe(motion_thread=thread, heartbeat_ms=None)
    assert ctx.worker_alive() is True, "心跳缺失就报活失败：登记线程健在才是权威依据"


def test_dead_motion_thread_is_detected_even_with_fresh_heartbeat():
    """motion 线程已死但心跳仍是旧的（陈旧值）-> 必须报假活不可信。"""
    dead = type("T", (), {"is_alive": lambda s: False})()
    fresh = int(time.time() * 1000)
    ctx = _CtxWithProbe(motion_thread=dead, heartbeat_ms=fresh)
    assert ctx.worker_alive() is False, "motion 线程已死却报活"


def test_stale_heartbeat_with_live_threads_is_not_a_false_fault():
    """回归核心：心跳陈旧 + 线程全健在（= 正在同步播放）-> 必须报活。

    修复前这条必红：``run_forever`` 在循环开头写心跳，播放期间不更新，
    4000ms 时间轴就会让心跳越过 5000ms 阈值。
    """
    live_motion = type("T", (), {"is_alive": lambda s: True})()
    stale = int(time.time() * 1000) - (WORKER_LIVENESS_MS + 3000)
    ctx = _CtxWithProbe(motion_thread=live_motion, heartbeat_ms=stale)
    assert ctx.worker_alive() is True, "播放期间心跳陈旧被判成故障（谎报）"


def test_really_dead_heartbeat_still_detected_when_motion_thread_unregistered():
    """motion 线程未登记（老路径 / 单测场景）-> 回退用心跳判定，死线程仍要暴露。"""
    stale = int(time.time() * 1000) - (WORKER_LIVENESS_MS + 3000)
    ctx = _CtxWithProbe(motion_thread=None, heartbeat_ms=stale)
    assert ctx.worker_alive() is False, "未登记且心跳陈旧时应报故障"


def test_live_heartbeat_still_reports_alive_without_motion_thread():
    """未登记但心跳新鲜 -> 报活（启动窗口内不得误报，对应第 2 组的回归护栏）。"""
    ctx = _CtxWithProbe(motion_thread=None, heartbeat_ms=int(time.time() * 1000))
    assert ctx.worker_alive() is True


def test_dead_domain_thread_still_reported_dead():
    """领域线程死亡必须报故障（第 2 组的核心成果，不得回退）。"""
    ctx = _CtxWithProbe(domain_alive=False,
                        motion_thread=type("T", (), {"is_alive": lambda s: True})(),
                        heartbeat_ms=int(time.time() * 1000))
    assert ctx.worker_alive() is False, "领域线程已死却报活"


def test_never_started_does_not_report_alive():
    """从未启动：不得报活（第 2 组成果）。"""
    ctx = _CtxWithProbe(domain_alive=False, motion_thread=None, heartbeat_ms=None)
    ctx._domain_threads = []
    assert ctx.worker_alive() is False


def test_real_playback_does_not_trip_the_probe():
    """端到端：真起一个同步阻塞 6.5s 的"播放"，探针全程必须报活。

    6.5s > WORKER_LIVENESS_MS(5000) > wave.json 4000ms 时间轴，
    覆盖真实生产场景的时长量级。
    """
    block_s = (WORKER_LIVENESS_MS / 1000) + 1.5
    beat = {"ms": None}

    def run_forever_like() -> None:
        """复刻 execution.run_forever 的心跳写位置：循环开头写，然后阻塞 play。"""
        while True:
            beat["ms"] = int(time.time() * 1000)
            time.sleep(block_s)

    thread = threading.Thread(target=run_forever_like, daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        ctx = _CtxWithProbe(motion_thread=thread, heartbeat_ms=beat["ms"])
        for _ in range(int(block_s) + 1):
            # 模拟播放期间 /status 被轮询
            ctx.worker_heartbeat_ms = beat["ms"]
            assert ctx.worker_alive() is True, "播放期间被判成故障"
            time.sleep(1.0)
    finally:
        pass  # daemon 线程随进程退出，无需 join


@pytest.mark.parametrize("thread_alive, heartbeat_age_ms, expected", [
    (True, 0, True),
    (True, WORKER_LIVENESS_MS - 1, True),
    (True, WORKER_LIVENESS_MS + 1, True),    # 播放中：心跳陈旧但线程健在 -> 报活
    (True, WORKER_LIVENESS_MS * 10, True),   # 极长播放：同上，仍报活
    (False, 0, False),                        # 线程死了：心跳再新鲜也要报故障
    (False, WORKER_LIVENESS_MS + 1, False),
])
def test_matrix(thread_alive: bool, heartbeat_age_ms: int, expected: bool) -> None:
    """决策矩阵：线程健在是必要且充分的，心跳只在未登记时才有话语权。"""
    t = type("T", (), {"is_alive": lambda s: thread_alive})()
    ctx = _CtxWithProbe(motion_thread=t,
                        heartbeat_ms=int(time.time() * 1000) - heartbeat_age_ms)
    assert ctx.worker_alive() is expected
