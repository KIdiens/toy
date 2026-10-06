"""第4 组 · 两个契约缺陷的护栏（判别力见每条 docstring）。

## 缺陷1：``due_at_utc``的 ``or ""`` 违反 openapi 的 ``format: date-time``

``reminders_repo.Reminder.due_at_utc`` 是 ``str | None``（DDL 里该列可空），
``openapi.yaml`` 却把它声明为 ``{type: string, format: date-time}``。
``dto.py`` 曾用 ``reminder.due_at_utc or ""`` 把None 洗成空串，而**空串不是
合法的 RFC 3339 date-time** —— 实测真实 ``GET /api/v1/reminders`` 200 响应里
就是 ``{"due_at_utc": ""}``。

修法：DTO 保留 ``None``；``ReminderOut`` 改``str | None``；openapi 标
``nullable: true``（本项目是 OpenAPI 3.0.3，3.0 表达可空就用 ``nullable``）。

## 缺陷2：``recorder.py`` 用 ``sample_rate`` 做除数/截断系数

采样率非正时，帧数除以它会ZeroDivisionError；即便时长已知，超限时
``keep = int(limit * 0) * 2 * channels == 0`` 会把整段 PCM **静默砍成空字节**
（实测 1600B → 0B）。

注意修法不是``rate = recorded.sample_rate or 1``：实测该修法把 1600B 截到
30B并把 ``has_speech`` 翻成 True —— 更坏的数据损坏。本组采用「不推导、不截断」。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from host.adapters.dto import reminder_dto
from host.adapters.runtime import build_context
from host.audio.backend import Recording
from host.audio.recorder import Recorder
from host.config.settings import load_settings
from host.storage import reminders_repo
from host.web.app import create_app

from tests.support import TOY_ROOT

# 单声道 800 帧 = 1600B；音量足够大，可通过静音阈值（120）。
LOUD_PCM = b"\x40\x10" * 800
LEGAL_RATE = 16000


# ------------------------------------------------------------------ 缺陷 1
def _client(tmp_path: Path):
    settings = load_settings({"DB_PATH": str(tmp_path / "toy.sqlite3")})
    context = build_context(settings, simulate=True, root=TOY_ROOT)
    return context, TestClient(create_app(context), raise_server_exceptions=False)


def _reminder_with_due(due_at_utc, *, tmp_path: Path) -> int:
    """通过真实仓储写入一条提醒，再直接改``due_at_utc``（模拟历史/外部写入的 NULL）。"""
    context, _ = _client(tmp_path)
    try:
        pid = context._resolve_profile("default")
        rid = reminders_repo.create(
            context.db, text="该喝水了", due_at_utc="2030-01-01T08:00:00Z",
            timezone="Asia/Shanghai", profile_id=pid, now="2026-10-07T00:00:00Z",
        )
        context.db.connect().execute(
            "UPDATE reminders SET due_at_utc = ? WHERE id = ?", (due_at_utc, rid))
        context.db.connect().commit()
        return rid
    finally:
        context.close()
        context.db.close_all()


def test_due_at_utc_none_is_null_not_empty_string_in_http(tmp_path: Path) -> None:
    """NULL 的 ``due_at_utc`` 在真实 HTTP 响应里必须是 ``null``，绝不是 ``""``。

    判别力：把 ``dto.py`` 改回 ``reminder.due_at_utc or ""`` 即转红 ——
    断言的是**实际响应体里的那个值**，不是「非空」这类恒真谓词。
    """
    _reminder_with_due(None, tmp_path=tmp_path)
    context, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/reminders", params={"profile_id": "default"})
        assert response.status_code == 200, response.text
        items = response.json()["data"]
        assert len(items) == 1, items
        assert items[0]["due_at_utc"] is None, (
            f"NULL 的 due_at_utc 被洗成了 {items[0]['due_at_utc']!r}；"
            "空串不是合法 RFC 3339 date-time"
        )
    finally:
        context.close()
        context.db.close_all()


def test_due_at_utc_empty_string_is_also_not_a_valid_date_time(tmp_path: Path) -> None:
    """DB 里存空串时，响应就是空串 —— 所以「合法路径」本来就不该产出空串。

    这条锁定的是**契约边界**本身：证明空串不是本系统承认的「无值」表示，
    从而让上一条「用 None 而不是 ''」的选择有据可依。
    """
    _reminder_with_due("", tmp_path=tmp_path)
    context, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/reminders", params={"profile_id": "default"})
        assert response.status_code == 200, response.text
        value = response.json()["data"][0]["due_at_utc"]
        assert value == "", value
        with pytest.raises(ValueError):
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    finally:
        context.close()
        context.db.close_all()


def test_due_at_utc_present_still_round_trips_as_valid_date_time(tmp_path: Path) -> None:
    """回归护栏：有值时必须仍是可解析的 date-time，不得被改成null。

    判别力：把 DTO 无条件改成 ``None``（``reminder.due_at_utc`` → ``None``）即转红。
    """
    _reminder_with_due("2030-01-01T08:00:00Z", tmp_path=tmp_path)
    context, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/reminders", params={"profile_id": "default"})
        assert response.status_code == 200, response.text
        value = response.json()["data"][0]["due_at_utc"]
        assert value == "2030-01-01T08:00:00Z", value
        # 真能解析成时间（而不是「看起来像」）
        assert dt.datetime.fromisoformat(value.replace("Z", "+00:00")) == dt.datetime(
            2030, 1, 1, 8, 0, tzinfo=dt.UTC)
    finally:
        context.close()
        context.db.close_all()


def test_reminder_out_model_accepts_none() -> None:
    """出参模型必须显式接受 ``None``（否则 DTO 改对也会 500）。

    判别力：把 ``ReminderOut.due_at_utc`` 改回 ``str`` 即转红（pydantic ValidationError）。
    """
    from host.storage.reminders_repo import Reminder
    from host.web import schemas

    reminder = Reminder(
        id=1, profile_id=1, text="x", due_at_utc=None, timezone="Asia/Shanghai",
        repeat_rule=None, status="pending", last_fired_at=None, idempotency_key=None)
    out = schemas.ReminderOut(**reminder_dto(reminder))
    assert out.due_at_utc is None


def test_openapi_marks_due_at_utc_nullable() -> None:
    """文档必须能表达「这个字段可能没有值」。

    判别力：删掉 ``nullable: true`` 即转红。这是读文档的**行为**断言
    （按文档生成客户端据此把字段当可空），不是对文件做字符串快照。
    """
    import re

    text = (TOY_ROOT / "docs" / "api" / "openapi.yaml").read_text(encoding="utf-8")
    block = text.split("\n    Reminder:\n", 1)[1].split("\n    ReminderCreateRequest:", 1)[0]
    match = re.search(r"^\s*due_at_utc:\s*\{(?P<body>[^}]*)\}", block, re.MULTILINE)
    assert match is not None, "Reminder schema 里找不到 due_at_utc"
    body = match.group("body")
    assert "nullable: true" in body, f"due_at_utc 未标nullable: true: {{{body}}}"
    assert "format: date-time" in body, f"due_at_utc 丢了 format: date-time: {{{body}}}"


# ------------------------------------------------------------------ 缺陷 2
class _FixedBackend:
    """回放预置 ``Recording`` 的后端桩。"""

    name = "fixed"

    def __init__(self, recorded: Recording) -> None:
        self._recorded = recorded

    def open(self) -> None: ...
    def close(self) -> None: ...
    def stop(self) -> None: ...
    def play(self, clip, *, volume: float = 1.0) -> None: ...

    def record(self, max_seconds: float) -> Recording:
        return self._recorded

    @property
    def busy(self) -> bool:
        return False


def test_zero_sample_rate_with_no_duration_does_not_raise() -> None:
    """``sample_rate=0`` + ``duration_s=0``：不得抛 ZeroDivisionError。

    判别力：回退到 ``... / recorded.sample_rate`` 即转红（ZeroDivisionError 穿透
    ``Recorder.record``）。断言的是「异常类型消失」，且同时校验音频数据**未被销毁**，
    因此「把除数换成 1」这类假修法无法蒙混过关。
    """
    recorded = Recording(data=LOUD_PCM, sample_rate=0, channels=1, duration_s=0.0)
    out = Recorder(_FixedBackend(recorded)).record()

    assert out.data == LOUD_PCM, (
        f"音频数据被改动：{len(LOUD_PCM)}B -> {len(out.data)}B")
    assert out.sample_rate == 0


def test_zero_sample_rate_does_not_silently_truncate_recording() -> None:
    """``sample_rate=0`` + 时长超限：不得把整段 PCM 砍成空字节。

    这是比除零更隐蔽的损坏：``keep = int(limit * 0) * 2 * channels == 0``。
    判别力：回退到修复前代码即转红（``data`` 变成 ``b""``）；
    用 ``rate or 1`` 也会转红（被截到 30B）。
    """
    recorded = Recording(data=LOUD_PCM, sample_rate=0, channels=1, duration_s=20.0)
    out = Recorder(_FixedBackend(recorded)).record()

    assert out.data == LOUD_PCM, (
        f"录音被静默截断：{len(LOUD_PCM)}B -> {len(out.data)}B")
    assert out.duration_s == 20.0


def test_illegal_sample_rate_does_not_flip_has_speech_true() -> None:
    """采样率非法时不得把 ``has_speech`` 翻成 True。

    判别力：``rate = sample_rate or 1`` 这条假修法在这里转红（实测它会算出
    15.0s 时长→ ``has_speech=True``，而真实语音内容其实存在，此处断的是
    「不得由非法采样率推导出的伪时长把结论带偏」）。
    """
    silent = b"\x00\x00" * 800
    recorded = Recording(data=silent, sample_rate=0, channels=1, duration_s=0.0)
    out = Recorder(_FixedBackend(recorded)).record()
    assert out.has_speech is False


def test_legal_sample_rate_paths_are_unchanged() -> None:
    """合法路径必须零行为变化：推导时长与上限截断都照旧生效。

    判别力：任何把「非法采样率兜底」误写成「对所有输入都跳过推导/截断」的改动
    （例如删掉整个截断分支）都会让本条转红。
    """
    # (a) 后端没给时长 -> 按帧数推导
    recorded = Recording(data=LOUD_PCM, sample_rate=LEGAL_RATE, channels=1, duration_s=0.0)
    out = Recorder(_FixedBackend(recorded)).record()
    assert out.duration_s == pytest.approx(len(LOUD_PCM) // 2 / LEGAL_RATE)
    assert out.data == LOUD_PCM
    assert out.has_speech is True

    # (b) 时长超限 -> 仍按上限截断（兜底不得吞掉这条正常路径）。
    # 上限取 0.02s：LOUD_PCM 只有 0.05s，必须让 limit 小于实际时长，
    # 否则 ``pcm[:keep]`` 的keep(64000B) > len(pcm)(1600B)，
    # 截断自然不会发生—— 那样这条断言就是恒真的假绿。
    over = Recording(data=LOUD_PCM, sample_rate=LEGAL_RATE, channels=1, duration_s=99.0)
    truncated = Recorder(_FixedBackend(over)).record(max_seconds=0.02)
    assert truncated.duration_s == pytest.approx(0.02)
    assert len(truncated.data) == int(0.02 * LEGAL_RATE) * 2
    assert len(truncated.data) < len(LOUD_PCM)