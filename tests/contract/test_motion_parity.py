"""动作校验 / 插值一致性：``host/motion/*`` 必须与冻结基线逐字对齐。

对应 Spec §12 步骤 1「含动作校验与旧 motion.py 的一致性比对」与架构 §5 兼容契约。
冻结基线不可达时整体 ``skip``（不伪造通过）。
"""
from __future__ import annotations

import copy
import random
from collections.abc import Mapping

import pytest
from host.motion.interpolation import sample as host_sample
from host.motion.validation import validate as host_validate

from tests.support import import_frozen_motion, load_wave

frozen = import_frozen_motion()

pytestmark = pytest.mark.skipif(
    frozen is None, reason="冻结基线 software/motion.py 不可达，跳过一致性比对"
)

BAD_VALUES = (1399, 1601, float("nan"), float("inf"), float("-inf"), True, False,
              "1500", None, [1500])


def _outcome(func, action):
    """返回 ``(异常类型名, 消息)`` 或 ``(None, 返回值)``。"""
    try:
        return None, func(action)
    except Exception as exc:  # noqa: BLE001  故意全捕获以便逐项比对
        return type(exc).__name__, str(exc)


def _build_corpus(seed: int = 20260921) -> list[tuple[str, dict]]:
    rng = random.Random(seed)
    base = load_wave()
    corpus: list[tuple[str, dict]] = [("pristine", copy.deepcopy(base))]
    for index in range(500):
        action = copy.deepcopy(base)
        kind = index % 10
        if kind == 0:
            action["schema"] = rng.choice([2, 1.0, "1", True, None])
        elif kind == 1:
            action["role_id"] = rng.choice([0, 4, 9, True, "1"])
        elif kind == 2:
            frame = action["frames"][rng.randrange(len(action["frames"]))]
            frame["t_ms"] = rng.choice([60001, -1, 1.5, "0", None])
        elif kind == 3:
            frame = action["frames"][rng.randrange(len(action["frames"]))]
            frame["us"][rng.randrange(4)] = rng.choice(BAD_VALUES)
        elif kind == 4:
            frame = action["frames"][rng.randrange(len(action["frames"]))]
            frame["us"] = frame["us"][: rng.randrange(1, 4)]
        elif kind == 5:
            action["frames"] = action["frames"][: rng.randrange(1, 3)]
        elif kind == 6:
            action["frames"][-1]["us"] = [1500, 1500, 1500, 1490]
        elif kind == 7:
            action["frames"][0]["us"] = [1500, 1500, 1510, 1500]
        elif kind == 8:
            action["frames"][1]["t_ms"] = rng.choice([0, 1, 100])
        else:
            action["frames"][rng.randrange(len(action["frames"]))]["t_ms"] = 100
        corpus.append((f"case-{index}", action))
    return corpus


CORPUS = _build_corpus()


@pytest.mark.parametrize("label,action", CORPUS, ids=[label for label, _ in CORPUS])
def test_validate_matches_frozen_baseline(label, action):
    host_exc, host_val = _outcome(host_validate, action)
    frozen_exc, frozen_val = _outcome(frozen.validate, action)
    assert host_exc == frozen_exc, f"{label}: 异常类型不一致 host={host_exc} frozen={frozen_exc}"
    assert host_val == frozen_val, f"{label}: 返回值不一致"


def test_sample_matches_frozen_baseline_for_valid_actions():
    rng = random.Random(7)
    compared = 0
    for label, action in CORPUS:
        try:
            host_validate(action)
        except ValueError:
            continue
        for _ in range(20):
            t_ms = rng.randrange(-500, 6000)
            assert host_sample(action, t_ms) == frozen.sample(action, t_ms), (
                f"{label}@{t_ms}: 插值不一致"
            )
            compared += 1
    assert compared > 0, "没有可比对的有效动作样本"


def test_boundary_messages_are_identical():
    base = load_wave()
    cases = []

    def _dup_time(action):
        action["frames"][1]["t_ms"] = 0

    def _range(action):
        action["frames"][1]["us"][0] = 1601

    for mutate in (lambda a: a.update(schema=2), lambda a: a.update(role_id=9),
                   _dup_time, _range):
        action = copy.deepcopy(base)
        mutate(action)
        cases.append(action)
    for action in cases:
        assert _outcome(host_validate, action) == _outcome(frozen.validate, action)


def test_non_mapping_frames_are_out_of_domain() -> None:
    """**域外输入声明**：非对象（非 Mapping）的 action / frame 不在 parity 契约内。

    冻结基线 ``software/motion.py::validate`` 对 ``frames: [0, 1]`` 会抛
    ``AttributeError: 'int' object has no attribute 'get'`` —— 那是基线的实现细节，
    不是契约。host 侧改为显式 ``ValueError``（可读、可定位第几帧），两者
    **不要求**异常类型或消息一致。

    因此本测试做两件事：

    1. 钉住「500 条 fuzz 语料里没有这类输入」—— 将来若有人把非 Mapping 的
       action/frame 混进 ``_build_corpus``，本条立刻转红，强制其显式决策
       （要么补齐 host/基线的异常对齐，要么承认域外、不要加进语料）；
    2. 钉住 host 侧对域外输入的拒绝方式是 ``ValueError`` 而不是 ``AttributeError``。
    """
    offenders: list[tuple[str, str]] = []
    for label, action in CORPUS:
        if not isinstance(action, Mapping):
            offenders.append((label, "action"))
            continue
        frames = action.get("frames")
        if isinstance(frames, list):
            for index, frame in enumerate(frames):
                if not isinstance(frame, Mapping):
                    offenders.append((label, f"frames[{index}]"))
    assert not offenders, (
        "500 条 parity 语料里混入了非 Mapping 的 action/frame："
        f"{offenders[:5]}。这类输入属**域外**（冻结基线抛 AttributeError），"
        "不得直接扩进语料；请先对齐两侧异常类型与消息，并更新本条声明。"
    )

    for out_of_domain in ([1, 2], {"schema": 1, "role_id": 1, "frames": [0, 1]}):
        with pytest.raises(ValueError) as excinfo:
            host_validate(out_of_domain)
        assert not isinstance(excinfo.value, AttributeError)
