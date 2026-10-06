"""第4 组 · 修法选择前的可行性实测（决定改法，不改产品代码）。

两个必须先量的问题：

Q1  若把 DTO 的 ``due_at_utc`` 改成 ``None``，``schemas.ReminderOut``
    （``due_at_utc: str``）会不会把它拒掉→ 500？
    若会，则「只改 DTO」是错的修法，必须连带把出参模型改成 ``str | None``。

Q2  建议修法 ``rate = recorded.sample_rate or 1`` 是否真的「零误伤」？
    ``sample_rate=0`` 时除数变1，``_frames(pcm, channels) / 1`` 会得到一个
    巨大的伪时长，随后 ``duration > limit`` 触发**截断分支**，
    可能把正常 PCM 砍到只剩几字节。必须实测截断后的数据长度。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]  # toy/ 仓库根
sys.path.insert(0, str(REPO))

from host.adapters.dto import reminder_dto  # noqa: E402
from host.audio.backend import Recording  # noqa: E402
from host.audio.recorder import Recorder  # noqa: E402
from host.storage.reminders_repo import Reminder  # noqa: E402
from host.web import schemas  # noqa: E402


def line(t: str) -> None:
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def q1() -> None:
    line("Q1：ReminderOut(due_at_utc: str) 能否接受 None？")
    r = Reminder(id=1, profile_id=1, text="x", due_at_utc=None, timezone="Asia/Shanghai",
                 repeat_rule=None, status="pending", last_fired_at=None, idempotency_key=None)
    #直接构造「DTO 改成返回 None 之后」的载荷，而不是再走一遍当前 DTO
    #（当前 DTO 会把 None 洗成 ""，那样测不到出参模型的约束）。
    payload = {**reminder_dto(r), "due_at_utc": None}
    try:
        out = schemas.ReminderOut(**payload)
        print(f"  ReminderOut(due_at_utc=None) -> OK, due_at_utc={out.due_at_utc!r}")
        print("  结论: 出参模型能接受 None（改 DTO 不会 500）")
    except Exception as exc:  # noqa: BLE001
        print(f"  ReminderOut(due_at_utc=None) -> {type(exc).__name__}")
        print(f"  报错详情: {exc}")
        print("  结论: **只改 DTO 会500** —— 出参模型必须一并改成 str | None")


def q2() -> None:
    line("Q2：`rate = sample_rate or 1` 是否零误伤？（截断实测）")

    class ZeroRateBackend:
        name = "zero-rate"

        def open(self) -> None: ...
        def close(self) -> None: ...
        def stop(self) -> None: ...
        def play(self, clip, *, volume: float = 1.0) -> None: ...

        def record(self, max_seconds: float) -> Recording:
            return Recording(data=b"\x01\x02" * 800, sample_rate=0, channels=1, duration_s=0.0)

        @property
        def busy(self) -> bool:
            return False

    #模拟修法：把 recorder._postprocess 的除数换成 `or 1`
    import host.audio.recorder as rec_mod

    original = rec_mod.Recorder._postprocess

    def patched(self, recorded, limit):
        pcm = recorded.data
        rate = recorded.sample_rate or 1          # ← 建议修法
        duration = recorded.duration_s or rec_mod._frames(pcm, recorded.channels) / rate
        if duration > limit + 1e-6:
            keep = int(limit * rate) * 2 * recorded.channels
            pcm = pcm[:keep]
            duration = limit
        has_speech = rec_mod._rms(pcm) >= self.policy.silence_rms_floor
        return Recording(data=pcm, sample_rate=recorded.sample_rate,
                         channels=recorded.channels, duration_s=duration,
                         clipped=False, has_speech=has_speech and duration >= 0.05)

    rec_mod.Recorder._postprocess = patched
    try:
        out = Recorder(ZeroRateBackend()).record()
        print("  输入 data=1600B (800 帧), sample_rate=0")
        print(f"  `or 1` 修法输出: data={len(out.data)}B duration_s={out.duration_s}")
        print(f"  数据被砍掉 {1600 - len(out.data)} 字节"
              f"（伪时长 = 800/1 = 800s > limit 15s → 触发截断）")
        print("  结论: **`or 1` 不是零误伤** —— 它把录音砍到只剩 limit*1 帧。")
        print("        但注意：sample_rate=0 本身已是**非法输入**，任何处理都是兜底。")
    finally:
        rec_mod.Recorder._postprocess = original

    # 对照：当前真实代码在同样输入下的行为
    try:
        Recorder(ZeroRateBackend()).record()
    except ZeroDivisionError as exc:
        print(f"\n  对照，当前代码: ZeroDivisionError: {exc}")


if __name__ == "__main__":
    q1()
    q2()