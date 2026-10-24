"""使用真实领域线程、空音频和模拟 MCU 验证半启动回收。"""
from __future__ import annotations

import importlib
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from host.adapters.runtime import build_context
from host.adapters.shutdown import stop_domain_workers
from host.config.settings import load_settings

from tests.support import TOY_ROOT

main = importlib.import_module("host.main")


def watch_connection_closes(monkeypatch, database):
    """在连接所属线程验证已关闭；跨线程查询会先触发线程限制，无法判定关闭。"""
    close = database.close
    verified = []

    def close_and_check():
        connection = getattr(database._local, "con", None)
        close()
        if connection is not None:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
            verified.append(threading.get_ident())

    monkeypatch.setattr(database, "close", close_and_check)
    return verified


@pytest.mark.parametrize("thread_name,after_start", [
    ("host-net", False), ("host-audio", False), ("host-tick", False),
    ("host-worker", False), ("host-audio", True), ("host-worker", True),
])
def test_partial_startup_releases_real_threads_and_devices(
    tmp_path, monkeypatch, thread_name, after_start,
):
    settings = load_settings({"DB_PATH": str(tmp_path / "toy.db"), "AI_PROVIDER": "stub",
                              "AUDIO_BACKEND": "null", "HOST_PORT": "0"})
    context = build_context(settings, simulate=True, root=TOY_ROOT)
    connection = context.db.connect()
    owned = []
    start = threading.Thread.start

    def start_or_fail(thread):
        if thread.name.startswith("host-"):
            owned.append(thread)
        if thread.name == thread_name:
            if after_start:
                start(thread)
            raise RuntimeError("injected thread start failure")
        start(thread)

    monkeypatch.setattr(main, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(main, "build_context", lambda *a, **k: context)
    monkeypatch.setattr(main.logging, "basicConfig", lambda *a, **k: None)
    monkeypatch.setattr(threading.Thread, "start", start_or_fail)
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: pytest.fail("失败启动了 Web"))
    try:
        assert main.main(["--simulate", "--audio", "null"]) == 3
        assert owned and not any(thread.is_alive() for thread in owned)
        assert not context._domain_threads and not context.worker_running
        assert not context.player.backend._open
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    finally:
        # 即使断言失败，也不能让本回归自身留下领域线程。
        context._worker_stop.set()
        for thread in owned:
            if thread.ident is not None:
                thread.join(2)
        context.close()


def test_audio_cancel_failure_still_joins_threads_and_closes_database(tmp_path, monkeypatch):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            simulate=True, root=TOY_ROOT)
    connection = context.db.connect()
    context.start_workers()
    owned = list(context._domain_threads)

    def fail():
        raise OSError("cancel device failed")

    monkeypatch.setattr(context.audio_control, "close", fail)
    try:
        with pytest.raises(OSError, match="cancel device failed"):
            context.close()
        assert not any(thread.is_alive() for thread in owned)
        assert not context._domain_threads
        assert not context.player.backend._open
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    finally:
        context._worker_stop.set()
        for thread in owned:
            thread.join(2)
        monkeypatch.undo()
        context.close()


def test_domain_timeout_keeps_reference_until_thread_really_exits():
    release = threading.Event()
    thread = threading.Thread(target=release.wait, name="owned-blocked-test", daemon=True)
    context = SimpleNamespace(_worker_stop=threading.Event(), _domain_threads=[thread],
                              audio_control=None)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="owned-blocked-test"):
            stop_domain_workers(context, timeout=0.01)
        assert context._worker_stop.is_set()
        assert context._domain_threads == [thread] and thread.is_alive()
    finally:
        release.set()
        thread.join(2)
        stop_domain_workers(context, timeout=1)
    assert not context._domain_threads


def test_domain_connection_is_closed_by_its_owner_before_global_cleanup(tmp_path, monkeypatch):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            simulate=True, root=TOY_ROOT)
    main_connection = context.db.connect()
    ready = threading.Event()
    closed_by = watch_connection_closes(monkeypatch, context.db)

    def tick(stop):
        context.db.connect()
        ready.set()
        stop.wait(2)

    monkeypatch.setattr(context, "_tick_loop", tick)
    try:
        context.start_workers()
        assert ready.wait(1)
        tick_thread = next(t for t in context._domain_threads if t.name == "host-tick")
        context.stop_workers()
        assert context.db._connections == [main_connection], "领域连接仍留在全局登记表"
        assert closed_by == [tick_thread.ident]
    finally:
        context.close()


def test_close_stops_registered_motion_before_closing_database(tmp_path, monkeypatch):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            simulate=True, root=TOY_ROOT)
    ready, stop = threading.Event(), threading.Event()
    closed_by = watch_connection_closes(monkeypatch, context.db)

    def dispatch():
        context.db.connect()
        ready.set()

    monkeypatch.setattr(context, "dispatch_bus", dispatch)
    worker = threading.Thread(target=context.run_forever, args=(stop,), daemon=True)
    context.register_motion_thread(worker)
    context.worker_running = True
    worker.start()
    try:
        assert ready.wait(1)
        context.close()
        assert not worker.is_alive(), "数据库清理前没有停止运动线程"
        assert not context.db._connections
        assert closed_by == [worker.ident]
    finally:
        stop.set()
        worker.join(2)
        context.close()
