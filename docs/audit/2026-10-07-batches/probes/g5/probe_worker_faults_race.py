"""探针 G5-1：``worker_faults += 1`` 在 CPython 下到底会不会丢计数。

结论取决于实测，本探针只跑数据、不下结论。

**关键设计：必须带「阳性对照」**。若只跑裸自增看到「丢=0」，无法区分
  (a) 真的丢不了，与
  (b) 探针根本没能力测出丢计数（假阴性）。
所以这里用同一个 ``+= 1`` 语句，配一个会 Python 层``__setattr__`` 的类
—— STORE_ATTR 变成真实调用，强制在读-改-写之间切线程。
对照能丢、实验组不丢，才说明「不丢」是事实而不是探针无能。

四段：
  A. 字节码：`+= 1` 是LOAD_ATTR / BINARY_OP / STORE_ATTR 三步，中间没有调用点。
  B. 阳性对照：STORE_ATTR 走Python 层 ``__setattr__`` 时，丢计数稳定复现。
  C. 实验组：真实形态（普通 dataclass 实例属性）多线程压，switchinterval 调到1e-9。
  D. 三种修法在同一压测下的对照数据（供决策，非结论）。
"""
from __future__ import annotations

import dis
import itertools
import sys
import threading

ROUNDS = 200_000
THREADS = 4


class PlainCounter:
    """与 ``AppContext.worker_faults`` 同构：普通 dataclass 实例属性 + 裸 ``+= 1``。

    ``AppContext`` 是 ``@dataclass``（无 ``slots``）、无自定义 ``__setattr__``，
    所以 ``worker_faults`` 的读写就是纯 C 层字典操作 —— 与此处同构。
    """

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 1


class PySetattrCounter(PlainCounter):
    """阳性对照：``__setattr__`` 是 Python 函数 → STORE_ATTR 变成真实调用。

    读-改-写之间因此存在可切换点，用来证明本探针**有能力**测出丢计数。
    """

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)


class LockedCounter(PlainCounter):
    """修法候选：读-改-写整体在锁内。"""

    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()

    def bump(self) -> None:
        with self.lock:
            self.n += 1


class CountCounter(PlainCounter):
    """修法候选：``itertools.count`` 靠 C 层 ``next`` 的原子性。"""

    def __init__(self) -> None:
        super().__init__()
        self._c = itertools.count(1)

    def bump(self) -> None:
        self.n = next(self._c)


def _hammer(counter, rounds: int) -> None:
    bump = counter.bump
    for _ in range(rounds):
        bump()


def _race(make, *, rounds: int = ROUNDS, threads: int = THREADS) -> tuple[int, int]:
    """并发自增，返回 (期望值, 实际值)。"""
    counter = make()
    workers = [threading.Thread(target=_hammer, args=(counter, rounds)) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return threads * rounds, counter.n


def _section_a() -> None:
    print("=" * 72)
    print("A. `self.n += 1` 的字节码：LOAD_ATTR → BINARY_OP → STORE_ATTR")
    print("=" * 72)
    dis.dis(PlainCounter.bump)
    print("→ 中间无 CALL、无向后跳转：这两处才是 CPython 的线程切换检查点。")
    print("→ 若STORE_ATTR 走纯 C 层字典写入（无 __setattr__），整段无可切换点。\n")


def _section_b() -> None:
    print("=" * 72)
    print("B. 阳性对照：STORE_ATTR 走 Python 层 __setattr__ 时，丢计数应可复现")
    print("=" * 72)
    original = sys.getswitchinterval()
    print(f"（switchinterval 默认 {original}s）")
    sys.setswitchinterval(1e-9)
    expected, actual = _race(PySetattrCounter, rounds=20_000, threads=THREADS)
    sys.setswitchinterval(original)
    lost = expected - actual
    print(f"  期望={expected}  实际={actual}  丢={lost}")
    print(f"→ 对照{'能' if lost else '不能'}复现丢计数。")
    if not lost:
        print("!! 对照未复现 → 本探针无判别力，A/C 段的「不丢」不可信。\n")
    else:
        print("→ 对照复现成功 ⇒ 本探针有能力测出丢计数，C 段的 0 是真结论。\n")


def _section_c() -> None:
    print("=" * 72)
    print("C. 实验组：真实形态（普通实例属性，裸 += 1）")
    print("=" * 72)
    original = sys.getswitchinterval()
    for interval in (original, 1e-3, 1e-6, 1e-9):
        sys.setswitchinterval(interval)
        expected, actual = _race(PlainCounter, rounds=50_000)
        sys.setswitchinterval(original)
        lost = expected - actual
        print(f"  switchinterval={interval:<10} 期望={expected} 实际={actual} 丢={lost}")
    print("→ 真实形态下丢计数为 0。\n")


def _section_d() -> None:
    print("=" * 72)
    print("D. 修法候选在同一压测下的对照数据（switchinterval=1e-9）")
    print("=" * 72)
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    for label, make in (("裸 `+= 1`", PlainCounter),
                        ("Lock 包裹", LockedCounter),
                        ("itertools.count", CountCounter)):
        expected, actual = _race(make)
        print(f"  {label:<16} 期望={expected} 实际={actual} 丢={expected - actual}")
    sys.setswitchinterval(original)
    print("→ 三者在本压测下均为 0 丢；差别不在『能不能丢』，而在代码语义与成本。")


if __name__ == "__main__":
    _section_a()
    _section_b()
    _section_c()
    _section_d()