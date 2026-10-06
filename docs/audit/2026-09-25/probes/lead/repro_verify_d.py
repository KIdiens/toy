"""独立复现复验员报的 D1（空串幂等键 → 500）与两个附带发现（自环迁移）。

运行：python _audit_lead/repro_verify_d.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TOY_ROOT = Path(r"F:/AI智能底座玩具/toy")
sys.path.insert(0, str(TOY_ROOT))

from host.adapters.queues import CommandRouter  # noqa: E402
from host.adapters.runtime import build_context  # noqa: E402
from host.config.settings import load_settings  # noqa: E402
from host.core.bus import EventBus  # noqa: E402
from host.core.engine import Engine  # noqa: E402
from host.core.events import CommandKind  # noqa: E402
from host.core.state_machine import SessionStateMachine, State, Trigger  # noqa: E402

SEP = "=" * 76
SCRATCH = Path(tempfile.mkdtemp(prefix="repro-ver-", dir=r"F:/AI智能底座玩具/_audit_lead"))


def d1_empty_key() -> bool:
    print(SEP)
    print("D1  空串幂等键重放 → 是否 500")
    print(SEP)
    settings = load_settings({"DB_PATH": str(SCRATCH / "d1.sqlite3")})
    ctx = build_context(settings, simulate=True, root=TOY_ROOT)
    try:
        payload = {"text": "喝水", "due_at_utc": "2030-01-01T00:00:00Z",
                   "profile_id": "default", "idempotency_key": ""}
        try:
            first = ctx.create_reminder(dict(payload))
            print(f"  第 1 次（idempotency_key=''）: 成功 id={first.get('id')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  第 1 次: {type(exc).__name__}: {exc}")
        try:
            second = ctx.create_reminder(dict(payload))
            print(f"  第 2 次（同空键重放）      : 成功 id={second.get('id')}")
            reproduced = False
        except Exception as exc:  # noqa: BLE001
            print(f"  第 2 次（同空键重放）      : {type(exc).__name__}: {exc}")
            reproduced = True
        print(f"  → 空串键重放失败（生产即 500）: {reproduced}")
        return reproduced
    finally:
        ctx.close()


def _volume_effect(state: State, generation: int) -> dict:
    machine = SessionStateMachine(initial=state, generation=generation)
    router = CommandRouter()
    engine = Engine(EventBus(), router, state_machine=machine)
    commands = engine.apply(Trigger.VOLUME_PRESS)
    kinds = [c.kind.value for c in commands]
    return {
        "before": state.value,
        "after": machine.state.value,
        "kinds": kinds,
        "net_request": CommandKind.NET_REQUEST.value in kinds,
        "play_audio": CommandKind.PLAY_AUDIO.value in kinds,
        "play_action": CommandKind.PLAY_ACTION.value in kinds,
    }


def self_loop() -> bool:
    print()
    print(SEP)
    print("附带发现  音量键是自环迁移，却仍下发 ENTER 命令")
    print(SEP)
    bad = False
    for state in (State.IDLE, State.THINKING, State.SPEAKING, State.PERFORMING):
        info = _volume_effect(state, 3)
        harmful = info["net_request"] or info["play_audio"] or info["play_action"]
        bad = bad or harmful
        print(f"  {info['before']:11s}--VOLUME_PRESS-->{info['after']:11s} 命令={info['kinds']}"
              f"{'   <<< 自环却下发了进入命令' if harmful else ''}")
    print(f"  → 音量键自环产生非预期副作用: {bad}")
    return bad


def main() -> int:
    d1 = d1_empty_key()
    loop = self_loop()
    print()
    print(SEP)
    print("结论")
    print(SEP)
    print(f"  D1 空串幂等键 → 500        : {'复现（确定缺陷）' if d1 else '未复现'}")
    print(f"  自环迁移下发进入命令        : {'复现（确定缺陷）' if loop else '未复现'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
