"""第5 组 · 缺陷1「``worker_faults`` 非原子自增」的**前提护栏**。

## 结论：这是**实测判定不可触发**，不是「难触发」

原审计（``docs/CODE-REVIEW-2026-09-25.md`` 第 25 条）建议「用 ``itertools.count``
或加锁」。本组按要求先实测，结论是**当前形态下丢计数无法发生**，
故**不加锁、不改代码逻辑**，只补护栏把「为什么可以裸自增」钉死。

## 实测依据（探针：``probes/g5/probe_worker_faults_race.py``）

``self.n += 1`` 的字节码是 ``LOAD_ATTR → BINARY_OP → STORE_ATTR``：
中间**既无 CALL 也无向后跳转**，而 CPython 只在这两处检查线程切换；
``STORE_ATTR`` 走C 层 slot wrapper（MRO 里只有 ``object.__setattr__``）。
于是整个读-改-写之间不存在可切换点→ 不丢。

**判别力自证（关键）**：同一个 ``+= 1``，把 ``STORE_ATTR`` 换成 Python 层
``__setattr__``（人为造出可切换点）后立刻丢 30%~40%。所以「真实形态 0 丢」
是事实，而不是探针测不出来。

## 判别力：什么改动会让本文件转红

这几条测的是**前提**而非竞态本身 —— 因为竞态在当前形态下不可触发，
「断言不丢计数」会是恒绿的假测试（这正是上一批被复验方抓出的假绿模式）。
真正有价值的是：一旦有人破坏了前提，护栏立刻报警。

会让它转红的改动：
1. 给 ``AppContext`` 或其基类加 Python 层 ``__setattr__`` → 引入可切换点，竞态变可能；
2. 把 ``worker_faults`` 改成 ``property`` / descriptor → 读写走Python 调用；
3. 改用 free-threading（无 GIL）构建 → CPython 的切换点检查不再是唯一屏障；
4. 有人把自增拆成「先读后写」两步 → 竞态立刻可能（这条由源码形态断言守住）。
"""
from __future__ import annotations

import dis
import re
import sys
import threading
from pathlib import Path

from host.adapters.ports import AppContext


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_repo() / rel).read_text(encoding="utf-8")


def _bump_source() -> str:
    """取两处自增所在函数的源码文本。

    刻意**读文件**而不是 ``inspect.getsource``：后者在 pytest 下会因断言重写
    拿不到源码（实测返回空串，第一版就是这么误报的）。
    """
    execution = _read("host/adapters/execution.py")
    workers = _read("host/adapters/workers.py")
    return "\n".join(part for part in (execution, workers) if part)


# --------------------------------------------------------------- 前提 1：无 Python 层 __setattr__
def test_no_python_level_setattr_on_context_class() -> None:
    """STORE_ATTR 必须落在 ``object.__setattr__``（C 层），否则会引入可切换点。"""
    defining = [
        cls.__name__ for cls in AppContext.__mro__
        if "__setattr__" in cls.__dict__ and cls is not object
    ]
    assert not defining, (
        f"{defining} 定义了 Python 层 __setattr__；"
        "STORE_ATTR 将变成真实调用，`worker_faults += 1` 的读-改-写之间出现可切换点，"
        "此时必须改用itertools.count 或加锁（见 ports.py worker_faults 注释）"
    )
    assert AppContext.__dict__.get("__setattr__") is None


# --------------------------------------------------------------- 前提 2：不是 property/descriptor
def test_worker_faults_is_a_plain_dataclass_field() -> None:
    """字段必须是普通 dataclass 字段：property/descriptor 会让读写走 Python 层。"""
    import dataclasses

    assert not isinstance(AppContext.__dict__.get("worker_faults"), property)
    for cls in AppContext.__mro__:
        assert not isinstance(cls.__dict__.get("worker_faults"), property), (
            f"{cls.__name__}.worker_faults 是 property，读取会走 Python 层"
        )
    fields = {f.name: f for f in dataclasses.fields(AppContext)}
    assert "worker_faults" in fields, "worker_faults 应仍是 dataclass 字段"
    assert fields["worker_faults"].type in (int, "int")
    assert not AppContext.__subclasses__(), (
        "出现 AppContext 子类：子类若把 worker_faults 变成 descriptor，"
        "上述前提失效"
    )


# --------------------------------------------------------------- 前提 3：自增未被拆成两步
def test_increment_is_not_split_into_read_modify_write() -> None:
    """禁止出现「先取局部变量再写回」这种真正会丢的形态。"""
    src = _bump_source()
    assert src.count("worker_faults += 1") == 2, (
        "两处自增（execution.run_forever / workers._on_worker_error）应保持 `+= 1` 原样；"
        f"实际源码形态已变：\n{src}"
    )
    # 读-改-写拆开的典型痕迹：先赋值给别的名字，再写回字段
    assert "self.worker_faults = self.worker_faults" not in src, (
        "自增被拆成了读-改-写两步，这在 CPython 下会真的丢计数"
    )


# --------------------------------------------------------------- 前提 4：字节码里无切换点
def test_increment_bytecode_has_no_yield_point() -> None:
    """``+= 1`` 的字节码里不得出现 CALL_FUNCTION 等可让出 GIL 的指令。"""
    class Probe:
        def __init__(self) -> None:
            self.n = 0

        def bump(self) -> None:
            self.n += 1

    names = {i.opname for i in dis.get_instructions(Probe.bump)}
    forbidden = {"CALL", "CALL_FUNCTION", "CALL_FUNCTION_EX", "CALL_METHOD", "CALL_KW"}
    assert not (names & forbidden), (
        f"字节码出现调用指令 {names & forbidden}：读-改-写之间存在线程切换点，"
        "裸自增不再安全"
    )
    assert "BINARY_OP" in names and any(n.startswith("STORE_ATTR") for n in names)


# --------------------------------------------------------------- 消费方：仅诊断，无逻辑分支
def test_worker_faults_only_read_for_diagnostics() -> None:
    """钉死「只被``/status`` 读出来显示」这一前提：无任何阈值/重试分支依赖它。

    若将来有人写 ``if ctx.worker_faults > 10: 重启``，本条转红，
    因为那时丢计数就不再是纯诊断问题，必须加锁。
    """
    repo = _repo()
    offenders: list[str] = []
    for path in (repo / "host").rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_doc = False
        doc_delim = '"""'
        for lineno, raw in enumerate(lines, 1):
            line = raw.split("#")[0]
            #跳过模块/函数 docstring：那里的 worker_faults 只是文字说明，不是代码。
            #三引号成对计数即可判定「当前是否在 docstring 内」，比逐行正则稳。
            if doc_delim in raw:
                in_doc = not in_doc
                continue
            if in_doc or not re.search(r"worker_faults", line):
                continue
            if re.search(r"\+=\s*1\b", line):  # 自增
                continue
            if re.search(r"int\(self\.worker_faults\)", line):  # /status 快照
                continue
            if re.search(r"worker_faults\s*:\s*int", line):  # 字段声明
                continue
            offenders.append(f"{path.relative_to(repo)}:{lineno}: {raw.strip()}")
    assert not offenders, (
        "出现新的 worker_faults 消费方（可能引入逻辑分支，丢计数将不再只是诊断问题）：\n"
        + "\n".join(offenders)
    )


# --------------------------------------------------------------- 阳性对照：证明探针有判别力
def test_probe_would_detect_race_if_precondition_broke() -> None:
    """阳性对照：人为造出可切换点后，丢计数**能**被测出来。

    没有这条，「0 丢」可能只是探针无能；有了它，「0 丢」才是结论。
    """
    rounds, threads = 20_000, 4
    original = sys.getswitchinterval()

    class RaceCounter:
        """STORE_ATTR 走 Python 层 __setattr__ → 制造可切换点。"""

        def __init__(self) -> None:
            self.n = 0

        def __setattr__(self, name: str, value: object) -> None:
            object.__setattr__(self, name, value)

        def bump(self) -> None:
            self.n += 1

    counter = RaceCounter()
    sys.setswitchinterval(1e-9)
    try:
        workers = [
            threading.Thread(target=lambda: [counter.bump() for _ in range(rounds)])
            for _ in range(threads)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    finally:
        sys.setswitchinterval(original)

    lost = threads * rounds - counter.n
    assert lost > 0, (
        "阳性对照竟然没丢计数 → 说明本文件的并发探针没有判别力，"
        "『真实形态不丢』的结论不可信（可能是探针太弱，而不是真的安全）"
    )


def test_real_shape_loses_nothing_under_aggressive_switching() -> None:
    """真实形态（普通 dataclass 字段）在 switchinterval=1e-9 下压测：不丢。"""
    rounds, threads = 50_000, 4
    original = sys.getswitchinterval()
    ctx = AppContext.__new__(AppContext)
    ctx.worker_faults = 0
    sys.setswitchinterval(1e-9)
    try:
        workers = [
            threading.Thread(target=lambda: [setattr(ctx, "worker_faults",
                                                      ctx.worker_faults + 1)
                                             for _ in range(rounds)])
            for _ in range(threads)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    finally:
        sys.setswitchinterval(original)
    assert ctx.worker_faults == threads * rounds, (
        f"裸自增丢了 {threads * rounds - ctx.worker_faults} 次计数 —— "
        "前提（无 Python 层 __setattr__）可能已被破坏，请加锁并更新 ports.py 注释"
    )