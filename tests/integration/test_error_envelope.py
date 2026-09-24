"""复查整改（第一批）回归护栏：统一错误信封（SPEC §5）。

三条实测可达的 500 此前是**纯文本** `Internal Server Error`，违反契约。现在必须：
可预期的错误 → 422 + `{code,data,message}`；未预期异常 → 500 兜底信封，
且响应体**不外泄**堆栈/路径/异常类名，而日志里必须保留全量堆栈。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app

from tests.support import TOY_ROOT

TOO_BIG = "9" * 19


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


def test_locked_database_write_never_returns_plain_text(tmp_path: Path) -> None:
    """写锁冲突下也必须回 JSON 信封，绝不允许纯文本 500。"""
    context, client = _client(tmp_path)
    context.db.connect().execute("PRAGMA busy_timeout = 150")
    blocker = sqlite3.connect(str(context.settings.storage.path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        response = client.post("/api/v1/reminders", json={
            "request_id": "e-2", "profile_id": "default", "text": "喝水",
            "due_at_utc": "2030-01-01T00:00:00Z", "timezone": "Asia/Shanghai"})
        assert response.headers["content-type"].startswith("application/json"), response.text
        _assert_envelope(response.json())
        assert response.status_code == 500, (
            f"写锁冲突下应当是被翻译过的 500 信封，实际 {response.status_code}：{response.text}"
        )
        assert response.json()["code"] == 50001, response.json()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        context.close()
