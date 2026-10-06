"""第4 组 · 复现脚本（先证明缺陷存在，再改代码）。

运行：``python data/audit_b4/repro_g4.py``（仓库根目录下）

覆盖两个契约缺陷：
  缺陷1 host/adapters/dto.py:38 ``due_at_utc or ""`` —— None 被伪装成空串，
        违反 openapi.yaml 声明的 ``format: date-time``。
  缺陷2 host/audio/recorder.py:69 ``... / recorded.sample_rate`` —— sample_rate=0
        且 duration_s 为0/None 时的 latent ZeroDivisionError。

纪律：全部走**真实** DTO 转换 / 真实 HTTP 响应 / 真实 Recorder.record()，
不读代码下结论。
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]  # toy/ 仓库根
sys.path.insert(0, str(REPO))

WORK = Path(__file__).resolve().parent / "_scratch"

from fastapi.testclient import TestClient  # noqa: E402
from host.adapters.dto import reminder_dto  # noqa: E402
from host.adapters.runtime import build_context  # noqa: E402
from host.audio.backend import Recording  # noqa: E402
from host.audio.recorder import Recorder  # noqa: E402
from host.config.settings import load_settings  # noqa: E402
from host.storage import reminders_repo  # noqa: E402
from host.web.app import create_app  # noqa: E402


def line(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------- 缺陷 1
def defect1() -> None:
    line("缺陷1：due_at_utc=None 时 DTO 吐出空串，违反 format: date-time")

    # 用固定目录而非 TemporaryDirectory：sqlite 文件句柄在Windows 上会延迟释放，
    # TemporaryDirectory 退出时unlink 会WinError 32。
    WORK.mkdir(parents=True, exist_ok=True)
    db_path = WORK / "repro_g4.sqlite3"
    if db_path.exists():
        db_path.unlink()
    settings = load_settings({"DB_PATH": str(db_path)})
    ctx = build_context(settings, simulate=True, root=REPO)
    try:
        pid = ctx._resolve_profile("default")
        # 直接写库造due_at_utc=NULL 的提醒（DDL 里该列可空，仓储层类型是 str|None）。
        rid = reminders_repo.create(
            ctx.db, text="该喝水了", due_at_utc="2030-01-01T08:00:00Z",
            timezone="Asia/Shanghai", profile_id=pid, now="2026-10-07T00:00:00Z",
        )
        ctx.db.connect().execute(
            "UPDATE reminders SET due_at_utc = NULL WHERE id = ?", (rid,)
        )
        ctx.db.connect().commit()

        row = reminders_repo.get(ctx.db, rid)
        print(f"仓储层读回: due_at_utc={row.due_at_utc!r}  (类型 str|None，允许 None)")

        dto = reminder_dto(row)
        print(f"DTO 转换后: due_at_utc={dto['due_at_utc']!r}")

        client = TestClient(create_app(ctx), raise_server_exceptions=False)
        resp = client.get("/api/v1/reminders", params={"profile_id": "default"})
        print(f"真实 HTTP: GET /api/v1/reminders -> {resp.status_code}")
        body = resp.json()
        item = body["data"][0]
        print("响应 JSON 片段:")
        print(json.dumps({"due_at_utc": item["due_at_utc"]}, ensure_ascii=False))

        value = item["due_at_utc"]
        print("\n判据（非空时必须可被 datetime 解析；空值必须是 None 而非\"\"）:")
        if value is None:
            print("  None -> 无值，如实表达（不是非法字面量）")
            print("  结论: 已修复")
        elif value == "":
            try:
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                print(f"  {value!r} -> 可解析为合法 date-time")
                print("  结论: 未复现")
            except ValueError as exc:
                print(f"  {value!r} -> ValueError: {exc}")
                print("  结论: **缺陷复现** —— 响应里出现了一个非法 date-time 字面量，")
                print("        且它正是 DTO 用 `or \"\"` 造出来的。")
        else:
            try:
                dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                print(f"  {value!r} -> 可解析为合法 date-time")
            except ValueError as exc:
                print(f"  {value!r} -> ValueError: {exc}（非空但非法，仍是缺陷）")
    finally:
        ctx.close()
        ctx.db.close_all()
    shutil.rmtree(WORK, ignore_errors=True)


# ---------------------------------------------------------------- 缺陷 2
def defect2() -> None:
    line("缺陷2：sample_rate=0 时 recorder.py:69 的 ZeroDivisionError")

    # 2a. Recording 能不能构造出 sample_rate=0？
    rec = Recording(data=b"\x00\x00" * 160, sample_rate=0, channels=1, duration_s=0.0)
    print(f"2a. Recording 构造成功: sample_rate={rec.sample_rate} "
          f"duration_s={rec.duration_s} data={len(rec.data)}B"
          f"  -> dataclass 无校验，**构造得出来**")

    # 2b. 走真实 Recorder.record()（后端只负责返回这个 Recording）
    class ZeroRateBackend:
        name = "zero-rate-probe"

        def open(self) -> None: ...
        def close(self) -> None: ...
        def stop(self) -> None: ...
        def play(self, clip, *, volume: float = 1.0) -> None: ...

        def record(self, max_seconds: float) -> Recording:
            return Recording(data=b"\x00\x00" * 160, sample_rate=0,
                             channels=1, duration_s=0.0)

        @property
        def busy(self) -> bool:
            return False

    recorder = Recorder(ZeroRateBackend())
    try:
        out = recorder.record()
        print(f"2b. record() 返回: {out}")
        print("  结论: 未复现")
    except ZeroDivisionError as exc:
        print(f"2b. record() 抛出 ZeroDivisionError: {exc}")
        print("  结论: **缺陷复现** —— 异常从 recorder.py:69 冒出来。")

    # 2c. 两个真实后端自己会不会产出 sample_rate=0？
    line("2c. 真实后端能否产出 sample_rate=0（决定这条是不是产品缺陷）")
    from host.audio.null_backend import NullAudioBackend
    from host.audio.sd_backend import SoundDeviceBackend

    print(f"NullAudioBackend.__init__ 默认 sample_rate="
          f"{NullAudioBackend().sample_rate}")
    print(f"SoundDeviceBackend.__init__ 默认 sample_rate="
          f"{SoundDeviceBackend().sample_rate}")
    try:
        # null_backend 构造器不校验 sample_rate，能造出 0 —— 看它record() 会怎样。
        nb = NullAudioBackend(sample_rate=0)
        nb.open()
        nb.record(1.0)
        print("NullAudioBackend(sample_rate=0).record() -> 成功")
    except ZeroDivisionError as exc:
        print(f"NullAudioBackend(sample_rate=0).record() -> ZeroDivisionError: {exc}")
        print("  注意:除零发生在 **后端内部**（null_backend.py:69 frames/self.sample_rate），")
        print("        不是 recorder.py:69 —— 后端自己先崩了。")


if __name__ == "__main__":
    defect1()
    defect2()