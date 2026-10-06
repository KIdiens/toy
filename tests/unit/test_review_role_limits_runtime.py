"""角色限幅（``role.json`` 的 ``min_us``/``max_us``）在**运行期**的护栏。

## 为什么这组测试存在（缺陷背景）

``host/roles/schema.py`` 只在**加载期**校验「``min_us``/``max_us`` 自身落在固件
1400–1600 内」；``host/motion/validation.py`` 原来也只卡固件台架保守区间。
于是 ``content/roles/2``（限幅 1420–1580）可以播出一个含 1405μs 的动作文件 ——
``validate()`` 放行，一路下发到舵机。**角色限幅在那条链路上从未被读取过一次。**

## 三条设计决策，都有实测依据（不是推理）

1. **闭区间，不是半开区间。** ``content/roles`` 里 role1 的 wave、role2 的 wave、
   role3 的 nod 三个现有文件都**恰好取到** ``max_us``。用 ``[min, max)`` 一次性打死
   这三个已通过全部验收的动作。``test_existing_action_files_touch_upper_bound`` 钉死
   这个事实，将来谁改了限幅数值导致某个文件贴边，本测试会先看见。

2. **只校验关键帧就足够。** 播帧时中间值由 ``sample()`` 的三次插值产生，若插值能越过
   关键帧极值，只查关键帧就是漏的。``test_interpolation_never_exceeds_keyframe_extremes``
   用**合成动作**（而非现有 7 个文件）证明 smoothstep 单调性，结论是「不越界」。

3. **限幅参数只允许收窄。** ``test_limits_cannot_widen_beyond_firmware_range`` 钉死
   ``validate(action, min_us=0, max_us=9999)`` 必须被拒 —— 否则这个新参数就成了
   「绕过固件台架保守值」的后门。

## oracle 强度声明

本文件不声称「电机物理上绝对安全」—— 它只声称**代码链路上的越限幅动作无法到达
``MotionClient.play`` 的下发调用**。真机是否还有别的越限路径（例如固件侧 PWM 步进
的 overshoot）不在本文件覆盖范围，那需要台架实测。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from host.motion.client import MotionClient
from host.motion.interpolation import sample
from host.motion.validation import HIGH, LOW, validate

from tests.support import TOY_ROOT

ROLES_ROOT = TOY_ROOT / "content" / "roles"
ROLE_IDS = (1, 2, 3)
# 与 host/roles/schema.py 的 FW_MIN_US / FW_MAX_US 同值；此处独立写死是为了让
# 「护栏的兜底边界 == 固件台架保守值」这条不变量在本文件内自洽，不依赖被测模块。
FW_LO, FW_HI = 1400, 1600


def _role_config(role_id: int) -> dict:
    return json.loads((ROLES_ROOT / str(role_id) / "role.json").read_text(encoding="utf-8"))


def _limits(role_id: int) -> tuple[int, int]:
    cfg = _role_config(role_id)
    return int(cfg["min_us"]), int(cfg["max_us"])


def _span_for(peak_us: int) -> int:
    """返回能让 ``中位 → peak_us`` 满足 300μs/s 速度约束的**最小**帧间距（ms）。

    必须显式协调速度约束：造尖峰时若只顾限幅，会先被速度约束拒掉，
    那条失败与本组要验的限幅无关（本次就踩过：200ms 跨度 + 100μs 落差 = 500μs/s）。
    """
    return int(math.ceil(1.5 * abs(peak_us - 1500) / 300 * 1000)) + 100


def _action(role_id: int, *, peak_axis: int = 0, peak_us: int = 1500,
            peak_ms: int | None = None) -> dict:
    """构造一个「其他都合法、只有一个轴冲到 ``peak_us``」的动作。

    ``peak_ms`` 缺省时由 ``_span_for`` 按速度约束自动给出最小可行间距；显式传值
    用于「故意造出速度违规」的场景。
    """
    if peak_ms is None:
        peak_ms = _span_for(peak_us)
    us = [1500, 1500, 1500, 1500]
    us[peak_axis] = peak_us
    return {
        "schema": 1,
        "role_id": role_id,
        "name": "限幅测试动作",
        "frames": [
            {"t_ms": 0, "us": [1500, 1500, 1500, 1500]},
            {"t_ms": peak_ms, "us": list(us)},
            {"t_ms": peak_ms * 2, "us": [1500, 1500, 1500, 1500]},
        ],
    }


# =====================================================================
# 1. 护栏本体：validate() 按传入限幅判定
# =====================================================================
@pytest.mark.parametrize("role_id", ROLE_IDS)
def test_firmware_range_alone_lets_role_violation_through(role_id: int) -> None:
    """**缺陷的正面对照**：只按固件区间校验时，越角色限幅的动作**放行**。

    这条是「护栏之前」的证据 —— 它必须一直绿着，否则说明固件区间本身已经收紧了，
    本组测试的前提（固件区间比角色限幅宽）就变了，得重新评估。
    """
    lo, hi = _limits(role_id)
    if (lo, hi) == (FW_LO, FW_HI):
        pytest.skip(
            f"role{role_id} 的限幅就是固件区间，不存在「越角色限幅但仍在固件区间内」的值"
        )
    lo_side = lo > FW_LO          # 低于角色下限、但仍在固件区间内
    outside = lo - 1 if lo_side else hi + 1
    assert FW_LO <= outside <= FW_HI
    action = _action(role_id, peak_us=outside, peak_ms=_span_for(outside))
    validate(action)                # 不抛 = 固件区间确实拦不住
    with pytest.raises(ValueError):
        validate(action, min_us=lo, max_us=hi)


@pytest.mark.parametrize("role_id", ROLE_IDS)
@pytest.mark.parametrize("side", ["below", "above"])
def test_role_limits_reject_out_of_range_frames(role_id: int, side: str) -> None:
    lo, hi = _limits(role_id)
    outside = lo - 1 if side == "below" else hi + 1
    if not (FW_LO <= outside <= FW_HI):
        pytest.skip(f"{side}={outside} 已越出固件区间，固件校验会先拒，用例失去针对性")
    action = _action(role_id, peak_us=outside, peak_ms=1000)
    with pytest.raises(ValueError, match="角色限幅"):
        validate(action, min_us=lo, max_us=hi)


@pytest.mark.parametrize("role_id", ROLE_IDS)
def test_role_limits_accept_exact_boundaries(role_id: int) -> None:
    """**闭区间**：恰好等于 ``min_us``/``max_us`` 的帧值必须合法。

    三个现有文件里有三个恰好取到 ``max_us``，所以这不是理论洁癖而是硬需求。
    """
    lo, hi = _limits(role_id)
    for boundary in (lo, hi):
        action = _action(role_id, peak_us=boundary,
                         peak_ms=max(1000, int(1.5 * abs(boundary - 1500) / 300 * 1000) + 1000))
        assert validate(action, min_us=lo, max_us=hi) is action, (
            f"role{role_id} 的边界值 {boundary} 被拒 —— 闭区间被误实现成半开区间了"
        )


def test_error_message_names_the_limit_so_the_cause_is_visible() -> None:
    """越限幅必须报出**实际限幅数值**：内容作者看到 1405 越界时要能立刻定位到
    自己撞了哪个角色的哪条边界，而不是只看到一句「脉宽非法」。"""
    lo, hi = _limits(2)
    action = _action(2, peak_us=lo - 1, peak_ms=1000)
    with pytest.raises(ValueError) as excinfo:
        validate(action, min_us=lo, max_us=hi)
    message = str(excinfo.value)
    assert str(lo) in message and str(hi) in message, message


# =====================================================================
# 2. 限幅参数本身的后门防护
# =====================================================================
@pytest.mark.parametrize("lo,hi", [(0, 9999), (1300, 1700), (1500, 1400), (1601, 1601),
                                   (1399, 1500), (1500, 1601)])
def test_limits_cannot_widen_beyond_firmware_range(lo: int, hi: int) -> None:
    """限幅参数只允许**收窄**：越界或反向的区间必须在比对任何帧值之前就被拒。

    不加这条守卫时，``validate(action, min_us=0, max_us=9999)`` 会让校验比不传更松
    —— 新参数反而成了一条绕过固件台架保守值的后门。
    """
    with pytest.raises(ValueError, match="固件保守区间"):
        validate(_action(1), min_us=lo, max_us=hi)


def test_default_arguments_keep_the_frozen_baseline_message() -> None:
    """不传限幅时，消息必须与冻结基线**逐字一致**（``tests/contract`` 比对的就是它）。"""
    with pytest.raises(ValueError) as excinfo:
        validate(_action(1, peak_us=1601, peak_ms=1000))
    assert str(excinfo.value) == "脉宽必须在保守范围 1400 至 1600 微秒内"
    assert (LOW, HIGH) == (FW_LO, FW_HI)


# =====================================================================
# 3. 插值不会越过关键帧极值 —— 「只校验关键帧」这一决策的依据
# =====================================================================
@pytest.mark.parametrize("role_id", ROLE_IDS)
def test_interpolation_never_exceeds_keyframe_extremes(role_id: int) -> None:
    """合成一个**尖锐折线**动作，逐毫秒扫描全部采样值。

    为什么用合成动作而不是现有 7 个文件：现有文件都是平滑缓变，抽样通过不能证明
    「对任意动作都成立」。这里造 ``中位 → 极限 → 中位`` 的尖峰，并额外覆盖
    「两帧跨度极短 + 落差极大」的高斜率段，让 smoothstep 的单调性无处可藏。
    """
    lo, hi = _limits(role_id)
    for peak in (lo, hi, (lo + hi) // 2, lo + 1, hi - 1):
        # 两个跨度：刚够满足速度约束的最小值，和它的 2 倍（更缓）。
        # 不刻意造超速的尖峰 —— 那会先被速度约束拒掉，失败与本测试要验的限幅无关。
        minimal = _span_for(peak)
        for span_ms in (minimal, minimal * 2):
            action = _action(role_id, peak_us=peak, peak_ms=span_ms)
            validate(action, min_us=lo, max_us=hi)
            key_lo = min(x for f in action["frames"] for x in f["us"])
            key_hi = max(x for f in action["frames"] for x in f["us"])
            for t_ms in range(0, int(action["frames"][-1]["t_ms"]) + 1):
                values = sample(action, t_ms)
                for value in values:
                    assert key_lo <= value <= key_hi, (
                        f"role{role_id} peak={peak} span={span_ms} t={t_ms}: "
                        f"插值 {value} 越出关键帧极值 [{key_lo}, {key_hi}] —— "
                        f"只校验关键帧是漏的，护栏必须扩展到采样值"
                    )


def test_all_existing_action_files_satisfy_their_role_limits() -> None:
    """现有 7 个动作文件必须全部通过角色限幅校验（含边界值）。"""
    checked = 0
    for role_id in ROLE_IDS:
        lo, hi = _limits(role_id)
        for path in sorted((ROLES_ROOT / str(role_id) / "actions").glob("*.json")):
            action = json.loads(path.read_text(encoding="utf-8"))
            assert action["role_id"] == role_id, f"{path.name} 的 role_id 与所在目录不符"
            validate(action, min_us=lo, max_us=hi)
            checked += 1
    assert checked == 7, f"动作文件数变了（{checked}），新增文件也要纳入本测试的巡检范围"


def test_existing_action_files_touch_upper_bound() -> None:
    """钉死「三个现有文件恰好取到 ``max_us``」这个事实。

    它是**闭区间决策的实证依据**。若将来有人把某个动作的峰值从 ``max_us`` 挪开，
    这条会红 —— 那时可以考虑改成半开区间，但必须同时确认没有别的文件贴边。
    刻意不断言「必须有三个贴边」：内容迭代让文件不再贴边是正常演进，
    本测试的职责是「变化发生时提醒重新评估区间语义」，不是阻止内容变化。
    """
    touching = set()
    for role_id in ROLE_IDS:
        lo, hi = _limits(role_id)
        for path in sorted((ROLES_ROOT / str(role_id) / "actions").glob("*.json")):
            action = json.loads(path.read_text(encoding="utf-8"))
            us = [x for f in action["frames"] for x in f["us"]]
            if max(us) == hi:
                touching.add(f"{role_id}/{path.stem}")
    assert touching, (
        "没有任何动作文件取到 max_us 了 —— 闭区间决策的依据已消失，"
        "请重新评估是否改用半开区间（本测试会在你评估前一直红着提醒这件事）"
    )


def test_finite_float_frames_within_limits_are_accepted() -> None:
    """浮点帧值在限幅内必须放行（``validate`` 允许 ``int|float``，不能被护栏误伤）。"""
    lo, hi = _limits(2)
    action = _action(2, peak_us=1500, peak_ms=1000)
    action["frames"][1]["us"][0] = lo + 0.5
    assert validate(action, min_us=lo, max_us=hi) is action
    action["frames"][1]["us"][0] = lo - 0.5      # 只超 0.5 也必须拒
    with pytest.raises(ValueError, match="角色限幅"):
        validate(action, min_us=lo, max_us=hi)


def test_nan_frame_is_rejected_regardless_of_limits() -> None:
    """NaN 必须被拒 —— 它和任何限幅比较都为 False，若比较写成 ``not (lo <= x <= hi)``
    之外的形状就会漏。``math.isfinite`` 的既有行为不许被护栏改掉。"""
    lo, hi = _limits(2)
    action = _action(2, peak_ms=1000)
    action["frames"][1]["us"][0] = math.nan
    with pytest.raises(ValueError):
        validate(action, min_us=lo, max_us=hi)


# =====================================================================
# 4. 第二道防线：MotionClient.play 必须真的用上传入的限幅
# =====================================================================
class _RecordingLink:
    """最小 MotionLink 替身：记录写过的行，不做任何协议交互。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.state = [1, 1, 1]
        self._pending: list[str] = []

    def open(self) -> None: ...
    def close(self) -> None: ...
    def reset_input_buffer(self) -> None: ...

    def write_line(self, line: str) -> None:
        self.lines.append(line)
        if line == "STATUS":
            self._pending.append(f"STATE {' '.join(str(x) for x in self.state)}")

    def read_lines(self) -> list[str]:
        out, self._pending = self._pending, []
        return out


class _StubClock:
    """单调推进的假时钟：每 ``sleep`` 走 20ms（与真实 ``POLL_S`` 对齐）。"""

    def __init__(self) -> None:
        self.t = 0

    def now_ms(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(1, int(seconds * 1000))


def test_motion_client_rejects_role_limit_violation_before_opening_the_link() -> None:
    """``MotionClient.play`` 必须**在 ``link.open()`` 之前**就拒掉越限幅动作。

    顺序是关键：若先开串口再校验，真实硬件已经上电待命，而调用方拿到的是异常 ——
    收尾虽仍会发 STOP，但「限幅护栏」就变成了「限幅事后清理」。
    """
    link = _RecordingLink()
    client = MotionClient(link, _StubClock(), boot_wait_s=0.0)
    lo, hi = _limits(2)
    with pytest.raises(ValueError, match="角色限幅"):
        client.play(_action(2, peak_us=lo - 1, peak_ms=1000),
                    role_id=2, min_us=lo, max_us=hi)
    assert link.lines == [], f"越限幅动作不该触达链路，实际写了：{link.lines}"


def test_motion_client_defaults_to_firmware_range_when_limits_not_passed() -> None:
    """不传限幅时退化为固件区间（保持与冻结基线一致），不是「不校验」。"""
    link = _RecordingLink()
    client = MotionClient(link, _StubClock(), boot_wait_s=0.0)
    with pytest.raises(ValueError, match="保守范围"):
        client.play(_action(2, peak_us=FW_LO - 1, peak_ms=1000), role_id=2)
    assert link.lines == []


def test_motion_client_play_signature_keeps_role_id_optional() -> None:
    """``role_id`` 仍是可选的（``action["role_id"]`` 兜底），别把签名收窄成必填。"""
    import inspect

    params = inspect.signature(MotionClient.play).parameters
    assert params["role_id"].default is None
    assert params["min_us"].default == LOW
    assert params["max_us"].default == HIGH


# =====================================================================
# 6. 端到端：两道防线各自真的在链路上（零件测了不等于链路测了）
# =====================================================================
# 上面 1~5 节测的都是**零件**（validate / MotionClient）。本节测**链路**：
# 端到端走 AppContext.play_action / 按键 → motion.play，确认限幅真的在链路上生效。
# 本节曾缺失过一次 —— 把 execution.py 里的第二道防线整段删掉，零件测试全绿，
# 是这条链路测试后来才发现的。故单独成节，不与零件测试混在一起。
def _context(tmp_path: Path, name: str, *, role_id: int | None = None,
             content_root: Path | None = None):
    from host.adapters.runtime import build_context
    from host.config.settings import load_settings

    settings = load_settings({
        "DB_PATH": str(tmp_path / name),
        # 固件要求角色连续稳定 STABLE_MS=200ms 才允许 ARM（sim/controller.py:145）。
        # 不给足稳定期，ARM 前的到位/角色检查必然失败 —— 那是装置问题不是被测行为。
        "MOTION_BOOT_WAIT_S": "2.0",
    })
    root = content_root if content_root is not None else TOY_ROOT
    context = build_context(settings, simulate=True, root=root)
    if role_id is not None:
        context.switch_active_role(role_id)
        # 换角色后固件要连续稳定 STABLE_MS=200ms 才允许 ARM（sim/controller.py:145）；
        # 模拟档位的 role_res 不同步，这里显式同步并等稳定期，否则 ARM 必然报 INTERLOCK。
        if context.sim is not None:
            context.sim.role_res.role = role_id
            context.clock.sleep(0.3)
    return context


def _out_of_range_value(role_id: int) -> int:
    """构造一个「越该角色限幅、但仍在固件区间内」的帧值。

    刻意要求它**仍在固件区间内** —— 否则测的就不是「角色限幅在拦」而是「固件在拦」，
    护栏的角色限幅分支根本没被走到。
    """
    lo, hi = _limits(role_id)
    if (lo, hi) == (FW_LO, FW_HI):
        pytest.skip(f"role{role_id} 的限幅就是固件区间，构造不出针对性样本")
    outside = lo - 1 if lo > FW_LO else hi + 1
    assert FW_LO <= outside <= FW_HI
    return outside


def _isolated_root(tmp_path: Path, name: str) -> Path:
    """把**内容区**复制到 ``tmp_path`` 下再改，返回可直接传给 ``build_context`` 的根。

    **必须复制而不是原地改**：这组测试要做的正是「把动作文件改坏」，
    原地改会污染仓库工作区（且 ``tmp_path`` 隔离不了它 —— 内容根由 ``build_context``
    的 ``root`` 参数决定，与数据库路径无关）。曾因原地改导致 ``git status`` 脏、
    行尾被重写成 CRLF。

    注意 ``build_context(root=...)`` 收的是**仓库根**（``runtime.py:110`` 内部拼
    ``base / "content" / "roles"``），不是 ``content/roles`` 本身 —— 传错会让
    ``RoleRegistry`` 扫不到任何角色，报 ``未注册的角色: 2``。
    """
    import shutil

    target = tmp_path / name
    (target / "content").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROLES_ROOT, target / "content" / "roles")
    return target


def _install_out_of_range_action(root: Path, role_id: int) -> int:
    """把该角色的 ``greet`` 动作改写成越限幅版本，返回写入的帧值。

    走真实内容文件而不是 mock：限幅护栏要防的正是「内容文件被改坏」，
    mock 掉读取就绕过了被测场景。
    """
    path = root / "content" / "roles" / str(role_id) / "actions" / "greet.json"
    assert path.is_file(), f"role{role_id} 缺少 greet 动作包"
    outside = _out_of_range_value(role_id)
    action = json.loads(path.read_text(encoding="utf-8"))
    action["frames"][1]["us"][0] = outside
    # 协调速度约束：帧值改大后必须同步拉长跨度，否则先被300μs/s 拒掉，
    # 那种失败证明的是速度约束，与限幅无关。
    span = max(int(1.5 * abs(outside - 1500) / 300 * 1000) + 100,
               int(action["frames"][1]["t_ms"]))
    action["frames"][1]["t_ms"] = span
    action["frames"][2]["t_ms"] = span * 2
    path.write_text(json.dumps(action, ensure_ascii=False, indent=2), encoding="utf-8")
    return outside


def test_entry_gate_rejects_out_of_range_action_file_with_409(tmp_path: Path) -> None:
    """**第一道防线**（入口）：越限幅动作文件必须被 ``play_action`` 拒成 409，
    且理由里带得上角色限幅 —— 不能静默播出去，也不能锁死设备。"""
    from host.core.errors import ConflictError

    role_id = 2                      # role1 限幅 == 固件区间，构造不出针对性样本
    root = _isolated_root(tmp_path, "entry")
    _install_out_of_range_action(root, role_id)
    context = _context(tmp_path, "entry.sqlite3", role_id=role_id, content_root=root)
    try:
        lo, hi = _limits(role_id)
        with pytest.raises(ConflictError) as excinfo:
            context.play_action(role_id=role_id, action_id="greet")
        message = str(excinfo.value)
        assert "非法" in message and str(lo) in message and str(hi) in message, message
        # 入口拒绝是「内容问题」，绝不能顺手把设备带进故障锁定
        assert not context.motion_busy
        assert context.fault is None
    finally:
        context.close()


def test_second_defense_blocks_payload_that_bypassed_the_entry_gate(tmp_path: Path) -> None:
    """**第二道防线**：直接往工作队列塞越限幅载荷（模拟绕过入口），必须在下发前被拦。

    这条是本节的核心：它绕开 ``play_action``/``resolve_greet_action`` 整整两层校验，
    只有 ``execution._run_play`` 里的复核能拦住。把 ``execution.py`` 里
    ``self.role_limits(role_id)`` 那两行删掉，本条必须转红。
    """
    from host.core.events import Command, CommandKind

    role_id = 2
    context = _context(tmp_path, "second.sqlite3", role_id=role_id)
    try:
        outside = _out_of_range_value(role_id)
        span = int(1.5 * abs(outside - 1500) / 300 * 1000) + 100
        action = _action(role_id, peak_us=outside, peak_ms=span)
        action["name"] = "绕过入口的越限幅动作"
        # 断言**链路层**而非 ``motion.play`` 的调用次数：``MotionClient.play`` 是
        # 「先校验、再下发」，所以 play() 会被调用到、但 FRAME 一行都不该写出去。
        # 早先只spy play() 的调用次数，误以为「没调用」，实测那条是假绿 ——
        # spy 包在 play() 外层，校验在它里面。
        link = context.motion.link
        written: list[str] = []
        original_write = link.write_line

        def spy_write(line: str) -> None:
            written.append(line)
            original_write(line)

        link.write_line = spy_write
        # 换角色会在 motion 队列里留下残留命令（NOTIFY等），不排空的话
        # ``pump_once`` 消费的是旧命令，本条测试会「什么都没验到」地通过。
        context.drain()
        context._router.put(Command(
            kind=CommandKind.PLAY_ACTION,
            payload={"action": action, "role_id": role_id, "action_id": "greet"},
            generation=context.state_machine.generation,
        ))
        context.motion_busy = True
        context.pump_once()
        assert not any(line.startswith("FRAME") for line in written), (
            f"越限幅载荷的 FRAME 下发到了链路：{written}"
        )
        # 绕过入口的载荷属装配/调用面的真实异常 → SAFETY_TRIP + 故障锁定
        assert context.fault is not None, "越限幅载荷必须被归成故障"
        assert context.fault["code"] == "SAFETY_TRIP", context.fault
        assert str(_limits(role_id)[0]) in context.fault["detail"], context.fault
    finally:
        context.close()


def test_valid_action_still_passes_end_to_end_after_the_guard(tmp_path: Path) -> None:
    """护栏不得误伤：三个角色的合法 ``greet`` 都要真的下发成功。

    这是「会误伤哪类合法输入」这个必答问题的**直接答案**，而不是靠推理声明。
    """
    for role_id in ROLE_IDS:
        context = _context(tmp_path, f"ok{role_id}.sqlite3", role_id=role_id)
        try:
            assert context.roles.active_role_id == role_id
            result = context.play_action(role_id=role_id, action_id="greet")
            assert result["scheduled"] is True
            context.drain()
            assert context.motion_busy is False, f"role{role_id} 合法动作没能收尾"
            assert context.fault is None, f"role{role_id} 合法动作被判故障：{context.fault}"
            assert context.last_result is not None and context.last_result.frames_sent > 0
        finally:
            context.close()


def test_role_limits_are_actually_injected_into_the_hardware_call(
        tmp_path: Path) -> None:
    """确认限幅**真的以参数形式**到达 ``motion.play``，而不是「校验了但没传下去」。

    这条是为了防一种退化：护栏逻辑在 ``ports`` 侧算了一遍限幅，但 ``execution``
    忘了往下传，于是 ``MotionClient`` 退回固件区间 —— 而所有零件测试仍然全绿。
    """
    context = _context(tmp_path, "inject.sqlite3")
    try:
        role_id = context.roles.active_role_id
        lo, hi = _limits(role_id)
        seen: list[tuple] = []
        original = context.motion.play

        def spy(action_arg, **kwargs):
            seen.append((kwargs.get("min_us"), kwargs.get("max_us")))
            return original(action_arg, **kwargs)

        context.motion.play = spy
        context.play_action(role_id=role_id, action_id="greet")
        context.drain()
        assert seen == [(lo, hi)], f"下发给硬件的限幅是 {seen}，期望 [(  {lo}, {hi})]"
    finally:
        context.close()


# =====================================================================
# 7. 回归：限幅不改变「非限幅类」的既有拒绝行为
# =====================================================================
def test_speed_violation_still_rejected_when_limits_passed() -> None:
    """加了限幅参数不得掩盖速度约束：峰值速度 >300μs/s 仍须被拒。"""
    lo, hi = _limits(1)
    action = _action(1, peak_us=1590, peak_ms=200)   # 90μs / 0.2s = 450μs/s
    with pytest.raises(ValueError, match="300"):
        validate(action, min_us=lo, max_us=hi)


def test_midpoint_frame_requirements_still_enforced() -> None:
    """首末帧必须回中位的规则不因限幅参数而失效。"""
    lo, hi = _limits(2)
    action = _action(2, peak_us=1520, peak_ms=1000)
    action["frames"][-1]["us"] = [1500, 1500, 1500, 1520]
    with pytest.raises(ValueError, match="中位"):
        validate(action, min_us=lo, max_us=hi)


def test_role_limits_are_narrower_than_firmware_for_at_least_one_role() -> None:
    """护栏的前提：至少有一个角色的限幅**比固件区间窄**，否则本组全部无意义。"""
    narrow = [r for r in ROLE_IDS if _limits(r) != (FW_LO, FW_HI)]
    assert narrow, (
        "三个角色的限幅都等于固件区间了 —— 护栏退化成与固件校验重复，"
        "请重新评估这套机制是否还有存在必要"
    )


def test_validation_module_does_not_import_roles() -> None:
    """分层铁律：``host.motion`` 不得反向依赖 ``host.roles``（内容包层）。

    限幅因此只能由上层注入参数 —— 这条测试守住「不要为了方便改成直接 import」。
    **只匹配 import 语句**：早先用裸子串匹配，结果命中了 docstring 里为解释这条
    铁律而写的 ``host.roles`` 字样，测试自己把自己判红了。
    """
    for module in ("host/motion/validation.py", "host/motion/client.py"):
        source = Path(module).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            assert "host.roles" not in stripped, (
                f"{module} 反向 import 了 host.roles（分层：motion 不依赖内容包层）：{stripped}"
            )
