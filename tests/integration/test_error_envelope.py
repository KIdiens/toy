"""复查整改（第一批）回归护栏：统一错误信封（SPEC §5）。

三条实测可达的 500 此前是**纯文本** `Internal Server Error`，违反契约。现在必须：
可预期的错误 → 422 + `{code,data,message}`；未预期异常 → 500 兜底信封，
且响应体**不外泄**堆栈/路径/异常类名，而日志里必须保留全量堆栈。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.core.errors import StorageError
from host.storage.database import BUSY_TIMEOUT_MS
from host.web.app import create_app

from tests.support import TOY_ROOT

TOO_BIG = "9" * 19
# 本组新增：**绝对**耗时上限，不是从BUSY_TIMEOUT_MS 推导的。
#
# 为什么必须写死：若写成 ``BUSY_TIMEOUT_MS/1000*3``，那么把 BUSY_TIMEOUT_MS 改回
# 5000 时阈值同步涨到 15s，「阻塞 5.5s」照样通过 —— 阈值跟着被测对象一起变，
# 测试就成了假绿（反向验证实测：5000 下耗时 5.58s 仍然全绿）。
# 上界要锚定**缺陷本身**（5.5s 独占连接 5秒），而不是锚定当前实现值。
# 留足余量以容纳 CI 抖动，但必须显著小于原始的 5.5s。
LOCK_BUSY_BUDGET_S = 2.0


def _client(tmp_path: Path):
    settings = load_settings({"DB_PATH": str(tmp_path / "toy.sqlite3")})
    context = build_context(settings, simulate=True, root=TOY_ROOT)
    return context, TestClient(create_app(context), raise_server_exceptions=False)


def _assert_envelope(payload: dict) -> None:
    assert {"code", "data", "message"} <= set(payload), f"不是统一信封: {payload}"
    assert isinstance(payload["message"], str) and payload["message"]


def test_invalid_due_at_returns_422_envelope(tmp_path: Path) -> None:
    context, client = _client(tmp_path)
    try:
        response = client.post("/api/v1/reminders", json={
            "request_id": "e-1", "profile_id": "default", "text": "喝水",
            "due_at_utc": "not-a-date", "timezone": "Asia/Shanghai"})
        assert response.status_code == 422, response.text
        _assert_envelope(response.json())
    finally:
        context.close()


def test_profile_id_out_of_range_returns_422_envelope(tmp_path: Path) -> None:
    context, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/memories", params={"profile_id": TOO_BIG})
        assert response.status_code == 422, response.text
        _assert_envelope(response.json())
    finally:
        context.close()


def test_reminder_id_out_of_range_returns_422_envelope(tmp_path: Path) -> None:
    context, client = _client(tmp_path)
    try:
        response = client.delete(f"/api/v1/reminders/{TOO_BIG}")
        assert response.status_code == 422, response.text
        _assert_envelope(response.json())
    finally:
        context.close()


def test_unexpected_exception_falls_back_to_envelope(tmp_path: Path, monkeypatch,
                                                     caplog) -> None:
    context, client = _client(tmp_path)
    try:
        def boom(*_args, **_kwargs):
            raise RuntimeError(r"内部路径 C:\secret\impl.py 第 42 行")

        monkeypatch.setattr(context, "list_memories", boom)

        with caplog.at_level(logging.ERROR, logger="host.web.app"):
            response = client.get("/api/v1/memories")

        assert response.status_code == 500
        body = response.json()
        _assert_envelope(body)
        assert body["code"] == 50000
        dumped = str(body)
        for leak in ("secret", "RuntimeError", "impl.py", "Traceback"):
            assert leak not in dumped, f"响应体外泄了 {leak!r}"
        assert any("Traceback" in r.exc_text or "boom" in r.getMessage()
                   for r in caplog.records if r.exc_info), "日志里必须保留全量堆栈"
    finally:
        context.close()


def test_locked_database_write_returns_503_with_retry_after(tmp_path: Path) -> None:
    """写锁冲突是**临时性资源竞争**：503 + Retry-After，且必须**快速失败**。

    为什么不是 500：500 表示「服务器内部错误」，重试无意义；而写锁冲突在对方事务
    提交后重试即必然成功，属于典型的可重试临时故障，语义上对应 503。
    """
    context, client = _client(tmp_path)
    blocker = sqlite3.connect(str(context.settings.storage.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        response = client.post("/api/v1/reminders", json={
            "request_id": "e-2", "profile_id": "default", "text": "喝水",
            "due_at_utc": "2030-01-01T00:00:00Z", "timezone": "Asia/Shanghai"})
        elapsed = time.perf_counter() - started
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        context.close()

    assert response.headers["content-type"].startswith("application/json"), response.text
    _assert_envelope(response.json())
    assert response.status_code == 503, (
        f"写锁冲突应当回 503（可重试的临时竞争），实际 {response.status_code}：{response.text}"
    )
    body = response.json()
    assert body["code"] == 50301, body
    assert body["code"] != 50001, "锁冲突不得再落回通用50001"
    # 契约要求给出「等多久再试」，否则调用方无从判断该立即重试还是退避。
    assert "retry-after" in {k.lower() for k in response.headers}, dict(response.headers)
    assert int(response.headers["Retry-After"]) >= 1, response.headers
    # **判别力核心**：必须快速失败。原实现让单个写请求独占一个 DB 连接 5.5s
    # （busy_timeout=5000），在 Pi 4B 上足以拖垮服务。仅断言状态码无法区分
    # 「503 但仍阻塞 5.5s」——那才是真正没修好的形态，所以必须有耗时上界。
    #
    # 两条互补断言，避免阈值与被测常量联动：
    # ① 上界为**绝对值**：撞锁后必须在一个固定的短预算内返回（快失败）；
    # ② 下界贴近 busy_timeout：确实等过一次才放弃，而不是压根没尝试写。
    assert elapsed < LOCK_BUSY_BUDGET_S, (
        f"锁冲突下耗时 {elapsed:.2f}s 超出快速失败预算 {LOCK_BUSY_BUDGET_S}s："
        "说明仍在阻塞等待 busy_timeout，未真正快速失败"
    )
    assert elapsed >= BUSY_TIMEOUT_MS / 1000 * 0.5, (
        f"锁冲突下耗时 {elapsed:.2f}s 过短（< busy_timeout 的一半）："
        "可能压根没走到写事务，测试就变成了在测别的东西"
    )


def test_locked_database_read_of_existing_profile_still_succeeds(tmp_path: Path) -> None:
    """对照组：WAL 下**纯读**（profile 已存在、不触发写）不受写锁影响，仍回 200。

    这条守住 D3 修复的边界——不能为了写路径快速失败，把读路径也一起降级成 503。
    """
    context, client = _client(tmp_path)
    client.get("/api/v1/memories")  # 预热：建出 default profile，使其后的读取是纯读
    blocker = sqlite3.connect(str(context.settings.storage.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        response = client.get("/api/v1/memories")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        context.close()

    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0, response.json()


def test_non_busy_storage_error_still_returns_500(tmp_path: Path, monkeypatch) -> None:
    """对照组：**非**锁冲突的存储故障必须仍是 500 + 50001。

    防止「把所有 500 都改成 503」这种过度修正：确定性故障（表不存在 / 磁盘坏了）
    重试无意义，必须留在 500。
    """
    context, client = _client(tmp_path)

    def boom(*_args, **_kwargs):
        raise StorageError("OperationalError: no such table: reminders")

    monkeypatch.setattr(context, "list_reminders", boom)
    try:
        response = client.get("/api/v1/reminders")
    finally:
        context.close()

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["code"] == 50001, body
    assert "retry-after" not in {k.lower() for k in response.headers}, (
        "确定性 500 不应带 Retry-After：重试无意义，带了会误导调用方"
    )
