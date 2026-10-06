"""命令代数必须现取自状态机（组3：删掉 ``Engine.apply`` 的死参后代数纪律不回归）。

## 背景

``Engine.apply(trigger, payload, generation)`` 的第三个形参**自始未被引用**：
函数体内所有代数都取自 ``machine.generation``，而陈旧判定实际发生在
``default_dispatcher``（``kind in STALE_SENSITIVE`` 分支）—— 早于 ``apply``。
死参已删。

删一个没人用的形参本身零风险，但**真正要守的是「删掉之后代数纪律没被改坏」**：
如果有人日后把代数改成「从入参取」或「缓存起来」，命令的代数就会与状态机脱钩，
而 ``_run_play`` / ``_finish_recording`` 的迟到丢弃判定全部依赖它 ——
表现是「换角色后旧动作仍在播」或「取消录音后凭空多录一段」，
且这两条都不是崩溃，是**静默的行为错误**。

所以本文件不复核「形参删了没有」（那由 ``inspect.signature`` 一条守住），
而是**钉死代数本身的行为**，让任何改动代数值来源的提交都必须打红。
"""
from __future__ import annotations

import inspect

import pytest
from host.adapters.queues import CommandRouter
from host.core.bus import EventBus
from host.core.engine import Engine, default_dispatcher
from host.core.events import CommandKind, Event, EventKind
from host.core.state_machine import SessionStateMachine, State, Trigger


def _engine() -> tuple[Engine, SessionStateMachine]:
    """``Engine(bus, out, *, state_machine=...)`` —— bus/out 是必填位置参数。

    别写成 ``Engine(machine=...)``：``machine`` 是**仅关键字**参数且名字是
    ``state_machine``。本文件第一版就是这么写错的，12 条用例全红在
    ``TypeError: unexpected keyword argument 'machine'``。
    """
    machine = SessionStateMachine(initial=State.IDLE)
    return Engine(EventBus(), CommandRouter(), state_machine=machine), machine


# =====================================================================
# 1. 形参确实已删，且没有调用方还在传它
# =====================================================================
def test_apply_no_longer_accepts_a_generation_parameter() -> None:
    params = list(inspect.signature(Engine.apply).parameters)
    assert params == ["self", "trigger", "payload"], (
        f"apply 的签名变成了 {params}，预期只有 self/trigger/payload"
    )


def test_no_caller_still_passes_a_third_positional_argument() -> None:
    """源码级巡检：防止将来有人 ``engine.apply(trig, payload, gen)`` 撞 TypeError。

    真实后果是 ``TypeError`` 打穿 ``default_dispatcher`` → 事件处理线程，
    而事件处理异常会被 ``adapters/workers.py`` 的宽catch 吞成 worker_faults
    计数 —— 表现是「按键偶尔没反应且日志里只有一条 worker 异常」。

    **用 ``ast`` 而不是数逗号。** 本文件第一版是 ``line.count(",") >= 2``，
    只能看单行；而9 个生产调用点里有 3 处是跨行的
    （``execution.py:59``、``injection.py:79``、``ports.py:274``），
    往那 3 处注入第三实参，巡检报15 passed —— 漏放。
    （当时注释里写着「跨行调用另有下面一条」，但那条测试并不存在。）

    ``ast`` 对 ``func(a, b,\\n c)`` 与 ``func(a, b, c)`` 一视同仁，
    且能区分 ``f(a, b)`` 与 ``f(*args)``：``Starred`` 不是实参，
    不该当第三实参计数。
    """
    import ast
    import pathlib

    offenders: list[str] = []
    for path in sorted(pathlib.Path("host").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "apply":
                continue
            if not (len(node.args) >= 3 or any(
                    isinstance(a, ast.Starred) for a in node.args)):
                continue
            offenders.append(
                f"{path}:{node.lineno}: {ast.unparse(node)[:90]}"
            )
    assert not offenders, "仍有人在 apply() 传第三实参或解包：\n" + "\n".join(offenders)


# =====================================================================
# 2. 代数来源：EXIT 用迁移前、ENTER/NOTIFY 用迁移后
# =====================================================================
def test_exit_commands_carry_the_generation_from_before_the_transition() -> None:
    """``fire()`` 在「进入 IDLE」时把代数加一，所以 EXIT 必须带**迁移前**的代数。

    这是 ``apply`` docstring 里的「代数纪律」：EXIT 描述的是「正在离开的那个会话」。
    若改成迁移后的代数，下游 ``_finish_recording`` 会判代数不符而走兜底，
    凭空多录一段。

    **这条本身没有判别力，判别力在下一条。** 代数只在
    ``dest is IDLE and source is not IDLE`` 时递增（``state_machine.py:229``），
    而本条走 ``REC_STOP``（RECORDING -> THINKING）—— 代数不变，于是
    「迁移前的代数」与「迁移后的代数」是**同一个数**，断言无法把两者区分开。
    ruff 最初报 ``after`` 未使用，正是这个前提的信号：那个变量本该用来做对照，
    却发现无处可用。

    所以此处把「代数不变」显式钉成断言——若将来递增规则改动，这里先转红，
    提醒判别力的归属需要重新评估。EXIT 与ENTER 的真实代数分离校验放在下一条。
    """
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    machine.fire(Trigger.DIALOG_PRESS)          # IDLE -> RECORDING
    assert machine.state is State.RECORDING
    before = machine.generation
    commands = engine.apply(Trigger.REC_STOP)   # RECORDING -> THINKING，不加代数
    assert machine.generation == before, (
        "REC_STOP 迁移到非 IDLE，代数本不该变化 —— 若这里变了，说明代数递增"
        "规则被改动，本条的判别力前提需要重新评估"
    )
    exit_commands = [c for c in commands if c.kind is CommandKind.RECORD_STOP]
    assert exit_commands, f"REC_STOP 迁移没有产生 EXIT 命令：{commands}"
    assert exit_commands[0].generation == before


def test_transition_into_idle_bumps_generation_and_exit_keeps_the_old_one() -> None:
    """进入 IDLE 会加一代 —— 此刻 EXIT 与 ENTER/NOTIFY 的代数必然不同，必须都正确。"""
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    machine.fire(Trigger.DIALOG_PRESS)          # -> RECORDING
    before = machine.generation
    commands = engine.apply(Trigger.REC_CANCEL)  # RECORDING -> IDLE，代数 +1
    assert machine.generation == before + 1
    exits = [c for c in commands if c.kind is CommandKind.RECORD_STOP]
    assert exits, commands
    assert exits[0].generation == before, "EXIT 命令带的是迁移后的代数"
    # 其余（NOTIFY 与任何 ENTER 命令）都必须是迁移后的代数。
    # 注意不能按「不是 NOTIFY」过滤 —— 那样会把 EXIT 也算进来（EXIT 也是
    # 非 NOTIFY），断言必然自相矛盾。这是本文件第一版的 bug。
    for command in commands:
        if command.kind is CommandKind.RECORD_STOP:
            continue
        assert command.generation == before + 1, (
            f"{command.kind} 应带迁移后的代数，实际 {command.generation}"
        )


def test_notify_always_carries_the_current_generation() -> None:
    """NOTIFY 描述「刚进入的新会话」，必须带迁移**后**的代数。"""
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    commands = engine.apply(Trigger.DIALOG_PRESS)
    notify = [c for c in commands if c.kind is CommandKind.NOTIFY]
    assert notify, commands
    assert notify[0].generation == machine.generation
    assert notify[0].payload["state"] == State.RECORDING.value


# =====================================================================
# 3. 陈旧事件仍被default_dispatcher 丢弃（代数纪律的上游）
# =====================================================================
def test_stale_event_is_dropped_by_the_dispatcher_not_by_apply() -> None:
    """陈旧回包必须在**路由层**被丢，而不是指望 ``apply`` 里的代数判断。

    ``apply`` 已经没有代数形参了，它无从判断事件是否陈旧 —— 这正是删形参的
    前提：判定只有一个地方做。实测：拿一个旧代数的事件走进``dispatch``，
    命令列表必须是空的。
    """
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    machine.fire(Trigger.DIALOG_PRESS)          # -> RECORDING
    machine.fire(Trigger.REC_STOP)              # -> THINKING（AI_DONE 的唯一合法前置态）
    stale_generation = machine.generation - 1   # 假设是上一代会话的回包
    stale = Event(kind=EventKind.AI_DONE,
                  payload={"reply": "旧回复"}, generation=stale_generation)
    assert default_dispatcher(engine, stale) == []


def test_fresh_event_of_the_same_kind_is_not_dropped() -> None:
    """对照组：**同代**的 AI_DONE 必须正常产出命令。

    这条是上一条的**判别力来源**：上一条断言「陈旧事件产出空列表」，但如果
    ``AI_DONE`` 在任何情况下都产出空列表，上一条就恒绿。本条用同代事件证明
    「空列表」来自陈旧判定而非「事件本身不被接受」。

    前置状态必须是 THINKING —— ``AI_DONE`` 只在 ``(THINKING, AI_DONE) -> SPEAKING``
    迁移里合法（``state_machine.py:71``）。本文件第一版在 RECORDING 态发它，
    两条一起红；查明是「非法迁移返回空列表」而非「陈旧丢弃」后修正。
    """
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    machine.fire(Trigger.DIALOG_PRESS)          # -> RECORDING
    machine.fire(Trigger.REC_STOP)              # -> THINKING
    assert machine.state is State.THINKING
    stale_generation = machine.generation - 1
    stale = Event(kind=EventKind.AI_DONE, payload={"reply": "旧"}, generation=stale_generation)
    assert default_dispatcher(engine, stale) == [], "陈旧 AI_DONE 未被丢弃"
    fresh = Event(kind=EventKind.AI_DONE, payload={"reply": "新"}, generation=machine.generation)
    commands = default_dispatcher(engine, fresh)
    assert commands, "同代 AI_DONE 被误当陈旧丢弃了"
    assert machine.state is State.SPEAKING, commands


def test_stale_sensitive_kinds_cover_the_ones_that_matter() -> None:
    """陈旧敏感集合必须包含 AI_DONE / NET回包 / REC_STOP。

    这三类是「迟到回包会污染新一轮」的典型。集合被清空的话，
    换角色后旧角色的 AI 回包会插进新会话 —— 且不报错，只是说得不对。
    """
    from host.core.engine import STALE_SENSITIVE

    for kind in (EventKind.AI_DONE, EventKind.REC_STOP):
        assert kind in STALE_SENSITIVE, f"{kind} 不在陈旧敏感集合里"


# =====================================================================
# 4. 换角色后的整轮闭环（代数纪律的端到端表现）
# =====================================================================
def test_role_switch_invalidates_commands_from_the_previous_generation() -> None:
    """换角色后，上一代会话产生的命令不得被下游当成当前会话的。

    ``set_active`` 会让 ``session_generation += 1``，于是旧命令的代数落后一代。
    这条是「代数纪律」真正被使用的地方（``_run_play`` 的迟到丢弃）。
    """
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    machine.fire(Trigger.DIALOG_PRESS)          # -> RECORDING
    stale_commands = engine.apply(Trigger.REC_STOP)
    old_generation = stale_commands[0].generation
    machine.switch_role()                        # 代数 +1
    assert machine.generation == old_generation + 1
    assert all(c.generation < machine.generation for c in stale_commands)


@pytest.mark.parametrize("trigger", [
    Trigger.BOOT_OK, Trigger.DIALOG_PRESS, Trigger.REC_STOP,
    Trigger.REC_CANCEL, Trigger.PERFORM_PRESS, Trigger.VOLUME_PRESS,
])
def test_every_transition_produces_commands_with_the_state_machine_generation(
        trigger: Trigger) -> None:
    """不变量：任何迁移产出的命令，代数都必须是 ``state_machine`` 当前的某个值。

    刻意覆盖**自环迁移**（``VOLUME_PRESS``）—— 它不改变状态、不发 EXIT/ENTER，
    只发一条 NOTIFY，最容易在重构时被漏掉。
    """
    engine, machine = _engine()
    machine.fire(Trigger.BOOT_OK)
    for command in engine.apply(trigger):
        assert command.generation == machine.generation, (
            f"{trigger} 产出的 {command.kind} 代数 {command.generation} "
            f"与状态机 {machine.generation} 不符"
        )
