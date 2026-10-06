"""第4 组 · 修法实测（recorder.py:69/71 两条除零/截断路径）。

背景：``sample_rate=0`` 时建议修法 ``rate = recorded.sample_rate or 1``
被实测证伪（会把 1600B 砍成 30B）。本探针比较三种候选修法在**两个**输入下的行为：

  输入 A：duration_s=0（后端没给时长）、sample_rate=0、data=1600B
  输入 B：duration_s=20.0（超过 limit 15）、sample_rate=0、data=1600B
          —— 这一路走的是**截断分支** recorder.py:71，用 sample_rate 算 keep

候选：
  现状        -> A: ZeroDivisionError；B: pcm 被砍成 0 字节（数据销毁）
  or 1        -> A: 砍成 30B；      B: 砍成 30B
  采样率非法时不推导时长、不截断 -> A: 原样保留；B: 原样保留
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]  # toy/ 仓库根
sys.path.insert(0, str(REPO))

from host.audio.backend import Recording  # noqa: E402
from host.audio.recorder import Recorder, _frames, _rms  # noqa: E402

DATA = b"\x01\x02" * 800  # 800 帧（单声道）= 1600B


class Stub:
    """只回放固定 Recording 的后端桩。"""

    name = "stub"

    def __init__(self, rec: Recording) -> None:
        self._rec = rec

    def open(self) -> None: ...
    def close(self) -> None: ...
    def stop(self) -> None: ...
    def play(self, clip, *, volume: float = 1.0) -> None: ...

    def record(self, max_seconds: float) -> Recording:
        return self._rec

    @property
    def busy(self) -> bool:
        return False


def run(label: str, rec: Recording) -> None:
    try:
        out = Recorder(Stub(rec)).record()
        print(f"  {label:<28} -> data={len(out.data):>4}B duration_s={out.duration_s} "
              f"sample_rate={out.sample_rate} has_speech={out.has_speech}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:<28} -> {type(exc).__name__}: {exc}")


def main() -> None:
    for case, rec in [
        ("A: duration_s=0, rate=0", Recording(data=DATA, sample_rate=0, channels=1, duration_s=0.0)),
        ("B: duration_s=20, rate=0", Recording(data=DATA, sample_rate=0, channels=1, duration_s=20.0)),
    ]:
        print(f"\n{case}  (data={len(DATA)}B, limit=15s)")
        run("现状代码", rec)

    # 候选修法：`or 1`
    import host.audio.recorder as m

    original = m.Recorder._postprocess

    def or_one(self, recorded, limit):
        pcm = recorded.data
        rate = recorded.sample_rate or 1
        duration = recorded.duration_s or m._frames(pcm, recorded.channels) / rate
        if duration > limit + 1e-6:
            pcm = pcm[:int(limit * rate) * 2 * recorded.channels]
            duration = limit
        return Recording(data=pcm, sample_rate=recorded.sample_rate,
                         channels=recorded.channels, duration_s=duration,
                         clipped=False,
                         has_speech=_rms(pcm) >= self.policy.silence_rms_floor and duration >= 0.05)

    m.Recorder._postprocess = or_one
    try:
        for case, rec in [
            ("A", Recording(data=DATA, sample_rate=0, channels=1, duration_s=0.0)),
            ("B", Recording(data=DATA, sample_rate=0, channels=1, duration_s=20.0)),
        ]:
            run(f"候选 or 1 ({case})", rec)
    finally:
        m.Recorder._postprocess = original

    # 候选修法：采样率非法 -> 不推导时长、不截断，数据原样保留
    for case, rec in [
        ("A", Recording(data=DATA, sample_rate=0, channels=1, duration_s=0.0)),
        ("B", Recording(data=DATA, sample_rate=0, channels=1, duration_s=20.0)),
    ]:
        rate = rec.sample_rate
        if rate > 0:
            dur = rec.duration_s or _frames(rec.data, rec.channels) / rate
        else:
            dur = rec.duration_s or 0.0
        print(f"  候选 采样率非法兜底 ({case})   -> data={len(rec.data):>4}B "
              f"duration_s={dur} sample_rate={rate}")

    # 合法路径必须零变化（回归护栏）
    print("\n合法输入对照（候选修法不得改变任何正常行为）:")
    for rate, dur in [(16000, 0.0), (16000, 20.0), (44100, 0.0), (8000, 0.05)]:
        rec = Recording(data=DATA, sample_rate=rate, channels=1, duration_s=dur)
        r = rate if rate > 0 else 1
        new = rec.duration_s or _frames(rec.data, rec.channels) / r
        old = rec.duration_s or _frames(rec.data, rec.channels) / rate
        flag = "同" if new == old else "**变了**"
        print(f"  rate={rate:<6} duration_s={dur:<6} 现状={old:<8} 修法后={new:<8} {flag}")


if __name__ == "__main__":
    main()