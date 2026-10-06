"""第二批整改 · 第 3 组：THINKING 换角色 + clear_fault 假可用态。

本文件是**先写的复现**：修复前必须全红，修复后必须全绿。

E1  ~~THINKING 期间换角色 → 永久驻留~~ —— **已证伪，不是缺陷**
    原始假设：``_enforce_thinking_timeout`` 兜底投的 ``AI_FAIL`` 会因
    「换角色已 +1 代数」而被 STALE_SENSITIVE 守卫当作迟到回包丢弃，
    导致永久驻留 THINKING。
    实测（scripts 诊断，进 pytest- 无关的独立脚本跑过）：
    换角色后 gen 1→2，兜底用 ``self.state_machine.generation``（**当前**代数）
    发布，drain 后正常回到 IDLE。守卫比对的是「事件代数 == 当前代数」，
    兜底事件取的本来就是当前代数，不存在错配。
    保留下面两条作为**不回归护栏**：万一将来有人把兜底改成捕获「进入 THINKING
    时的旧代数」，这两条会立刻转红。

    **第 6 组补记（重要）**：上面证伪的是「永久驻留」，**不等于**「换角色在
    THINKING 中是安全的」—— 另一条独立缺陷（D7）是换角色后状态**静默留在
    THINKING 直到 60s 兜底**，即「不会永久驻留，但要白等一分钟」。该缺陷已修
    （``switch_active_role`` 在 THINKING 中先取消本轮再切），因此本文件两条护栏的
    **触发路径**（换角色后仍停在 THINKING）已不复存在，改用「代数推进但状态仍是
    THINKING」。**断言未放松，反而加码**（新增「兜底必须用当前代数发布」）。
    详见两条用例的 docstring。

E2  clear_fault 不校验硬件 → 假可用态（**成立，已复现**）
    固件语义（``sim/controller.py:loop_once``）：``armed and not docked()``
    会立即 ``stop_motion()`` 并回 ``FAULT INTERLOCK_OR_TIMEOUT``。
    而 ``clear_fault`` 只看状态机是否在 FAULT_LOCKED，不看公仔是否插到位、
    急停是否健康 —— 于是「公仔仍拔出」也能确认解除、回 IDLE、UI 显示已恢复，
    紧接着固件再次跳闸。用户看到的是「我解除了但马上又坏了」。
"""
from __future__ import annotations

import pytest
from host.core.events import EventKind
from host.core.state_machine import State


@pytest.fixture()
def ctx(sim_context):
    return sim_context


def _enter_thinking(ctx) -> None:
    """两次对话键：RECORDING → THINKING。"""
    ctx.press_button(1)
    ctx.drain()
    ctx.press_button(1)
    ctx.drain()
    assert ctx.state_machine.state is State.THINKING, (
        f"前置条件不成立：当前 {ctx.state_machine.state.value}"
    )


# --------------------------------------------------------------------------- E1 护栏
def test_thinking_timeout_survives_role_switch(ctx):
    """【护栏，非缺陷】THINKING 期间的 60s 兜底必须仍能把状态带回 IDLE。

    **第 6 组改动：触发路径已换，断言一条未放松。**
    原触发路径是「THINKING 中换角色 -> 仍停在 THINKING -> 60s 后兜底」。但第 6 组
    修掉了D7（换角色在 THINKING 中静默滞留60s），``switch_active_role`` 现在会先
    取消本轮当场收敛到 IDLE，那条路径**已不复存在**（实测：兜底连触发都没触发，
    因为 ``_enforce_thinking_timeout`` 第一道 ``if state is not THINKING`` 就返回了）。

    但护栏要守的性质——「兜底发布的 AI_FAIL 必须带**当前**代数，否则会被
    STALE_SENSITIVE 当成迟到回包丢弃」——依然真实且必要：实测若兜底改用旧代数，
    ``AI_FAIL`` 确实会被丢弃（``AI_FAIL in STALE_SENSITIVE == True``，旧代数 != 当前代数）。
    故改用「代数推进了但状态仍是 THINKING」这条路径（``bump_generation()``，即任何
    将来新增的代数递增路径）继续守同一条性质。
    """
    ctx.start_workers()
    try:
        _enter_thinking(ctx)
        gen_before = ctx.state_machine.generation
        # 代数推进但**不**离开 THINKING —— 兜底必须用新代数发布，否则被当迟到回包丢弃
        ctx.state_machine.bump_generation()
        assert ctx.state_machine.generation > gen_before, "代数未推进，前置无效"
        assert ctx.state_machine.state is State.THINKING, "前置无效：已离开 THINKING"

        ctx._thinking_since_ms = ctx.clock.now_ms() - 61_000
        ctx._enforce_thinking_timeout()
        ctx.drain()

        assert ctx.state_machine.state is not State.THINKING, (
            "60s 兜底失效：若AI_FAIL 改用「进入 THINKING 时的旧代数」发布，"
            "会被 STALE_SENSITIVE 当作迟到回包丢弃，设备永久驻留 THINKING"
        )
    finally:
        ctx.stop_workers()


def test_thinking_exit_does_not_speak_previous_role_reply(ctx, monkeypatch):
    """【护栏，非缺陷】兜底只回 IDLE，绝不把上一轮回复在新角色下说出来。

    **第 6 组改动：触发路径同上已换，断言一条未放松**（理由见上一条 docstring）。
    """
    ctx.start_workers()
    try:
        _enter_thinking(ctx)
        stale_generation = ctx.state_machine.generation
        # 让「上一轮回复」落在stale_generation 这一代，再推进代数模拟换角色
        ctx.state_machine.bump_generation()
        assert ctx.state_machine.state is State.THINKING
        ctx._thinking_since_ms = ctx.clock.now_ms() - 61_000

        spoke = []
        original = ctx._publish

        def spy(kind, payload, generation):
            spoke.append((kind, dict(payload), generation))
            return original(kind, payload, generation)

        monkeypatch.setattr(ctx, "_publish", spy)
        ctx._enforce_thinking_timeout()
        ctx.drain()

        assert ctx.state_machine.state is State.IDLE
        kinds = [k for k, _p, _g in spoke]
        assert EventKind.AI_FAIL in kinds, f"未回投 AI_FAIL，实际 {kinds}"
        assert EventKind.AI_DONE not in kinds, (
            f"兜底路径投了 AI_DONE（会把旧回复说出来）：{kinds}"
        )
        # 兜底必须用**当前**代数发布：否则这条 AI_FAIL 会被 STALE_SENSITIVE 丢弃
        current = ctx.state_machine.generation
        fail_gens = [g for k, _p, g in spoke if k is EventKind.AI_FAIL]
        assert fail_gens and all(g == fail_gens[-1] for g in fail_gens), (
            f"AI_FAIL 的代数应统一（当前 {current}），实际 {fail_gens}"
        )
        assert fail_gens[-1] != stale_generation, (
            "兜底用了「进入 THINKING 时的旧代数」发布 —— 该事件会被 "
            "STALE_SENSITIVE 当作迟到回包丢弃，兜底形同虚设"
        )
    finally:
        ctx.stop_workers()


# --------------------------------------------------------------------------- E2
def test_clear_fault_rejected_while_undocked(ctx):
    """公仔仍拔出时不得允许解除故障（否则立刻再次跳闸，假可用态）。"""
    ctx.inject_undock()
    ctx.drain()
    assert ctx.state_machine.state is State.FAULT_LOCKED, (
        f"前置条件不成立：当前 {ctx.state_machine.state.value}"
    )

    result = ctx.clear_fault()

    assert result.get("cleared") is False, (
        "公仔仍处于拔出状态却允许解除故障 —— 固件会在下一轮 loop_once 立刻"
        "重新输出 FAULT INTERLOCK_OR_TIMEOUT，用户看到「刚恢复又坏了」"
    )
    assert ctx.state_machine.state is State.FAULT_LOCKED, "未解除时状态不应变化"


def test_clear_fault_rejected_while_estop_unhealthy(ctx):
    """急停不健康时同样不得解除。"""
    if ctx.sim is None:
        pytest.skip("无 MCU 模拟器档位，急停状态不可控")
    ctx.trip_safety("INTERLOCK", "测试跳闸")
    ctx.drain()
    assert ctx.state_machine.state is State.FAULT_LOCKED

    ctx.sim.estop.pressed = True   # 急停按下 => healthy False
    try:
        result = ctx.clear_fault()
    finally:
        ctx.sim.estop.pressed = False  # 复位夹具，避免污染后续用例

    assert result.get("cleared") is False, "急停仍按下却允许解除故障 —— 同样是假可用态"
    assert ctx.state_machine.state is State.FAULT_LOCKED


def test_clear_fault_works_when_hardware_ok(ctx):
    """硬件正常时解除必须仍然可用（防过度收紧误伤正常路径）。"""
    ctx.trip_safety("TIMEOUT", "运动看门狗超时")
    ctx.drain()
    assert ctx.state_machine.state is State.FAULT_LOCKED

    result = ctx.clear_fault()
    assert result.get("cleared") is True, "硬件正常时解除故障被误拒 —— 过度收紧"
    assert ctx.state_machine.state is not State.FAULT_LOCKED


def test_clear_fault_rejection_explains_reason(ctx):
    """拒绝时必须给出可执行的原因（前端要显示，不是静默失败）。"""
    ctx.inject_undock()
    ctx.drain()
    result = ctx.clear_fault()
    assert result.get("cleared") is False
    reason = result.get("reason") or result.get("detail") or ""
    assert reason, "拒绝解除却没给原因 —— 前端无从提示用户"


def test_clear_fault_ignores_when_not_locked(ctx):
    """非锁定态调用仍按原语义返回 cleared=False（不回归）。"""
    ctx.drain()
    result = ctx.clear_fault()
    assert result.get("cleared") is False


# --------------------------------------------------------------------------- 死锁护栏
def test_undock_then_dock_allows_recovery(ctx):
    """【死锁护栏】拒绝解除之后必须还有出路：复位硬件 → 解除成功。

    ``clear_fault`` 加了硬件校验后，若没有 ``inject_dock`` 这类复位入口，
    「拔出后忘记复位」就会变成无法自救的死锁——这条用例锁住该出口。
    """
    ctx.inject_undock()
    ctx.drain()
    assert ctx.clear_fault().get("cleared") is False, "前置：未复位时必须拒绝解除"

    ctx.inject_dock()
    result = ctx.clear_fault()
    assert result.get("cleared") is True, (
        "硬件已复位却仍无法解除故障 —— 用户被锁死在 FAULT_LOCKED，无自救出路"
    )
    assert ctx.state_machine.state is not State.FAULT_LOCKED


def test_dock_alone_does_not_clear_fault(ctx):
    """复位硬件不等于解除故障：仍须人工确认（守住「绝不自动恢复」）。"""
    ctx.inject_undock()
    ctx.drain()
    ctx.inject_dock()
    assert ctx.state_machine.state is State.FAULT_LOCKED, (
        "仅复位硬件就自动解除了故障 —— 违反「绝不自动恢复」纪律"
    )
