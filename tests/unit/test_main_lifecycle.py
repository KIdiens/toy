"""入口中途失败仍要收尾，清理异常不能覆盖真正的启动/服务异常。"""
from __future__ import annotations

import importlib

import pytest
from host.adapters.runtime import build_context
from host.config.settings import load_settings

from tests.support import TOY_ROOT

main = importlib.import_module("host.main")


class ThreadStub:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.ident = None
        self.joined = False

    def start(self):
        if self.failure:
            raise self.failure
        self.ident = 123

    def join(self, timeout=None):
        assert self.ident is not None, "不能 join 尚未启动的线程"
        self.joined = True

    def is_alive(self):
        return False


class ContextStub:
    simulate = True
    worker_running = False

    def __init__(self, *, close_error=None):
        self.close_error = close_error
        self.closed = False
        self.domains_started = False

    def start_workers(self):
        self.domains_started = True

    def register_motion_thread(self, worker):
        self.worker = worker

    def run_forever(self, stop_event):
        pass

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def install(monkeypatch, *, worker, context):
    settings = load_settings({"HOST_PORT": "0", "LOG_LEVEL": "INFO"})
    monkeypatch.setattr(main, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(main, "build_context", lambda *a, **k: context)
    monkeypatch.setattr(main, "create_app", lambda ctx: object())
    monkeypatch.setattr(main.threading, "Thread", lambda **kwargs: worker)
    monkeypatch.setattr(main.logging, "basicConfig", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: calls.append(True))
    return calls


@pytest.mark.parametrize("failure", [RuntimeError("cannot start thread"), TypeError("thread bug")])
def test_motion_start_failure_closes_already_started_context(monkeypatch, failure):
    worker, context = ThreadStub(failure=failure), ContextStub()
    calls = install(monkeypatch, worker=worker, context=context)
    code, escaped = None, None
    try:
        code = main.main(["--simulate"])
    except Exception as exc:
        escaped = exc
    assert context.domains_started and context.closed, "运动线程启动失败后领域资源未收尾"
    assert not worker.joined and not calls
    if isinstance(failure, TypeError):
        assert escaped is failure, "编程错误必须保持原异常"
    else:
        assert escaped is None and code == 3


def test_service_error_survives_cleanup_error_and_worker_is_joined(monkeypatch):
    worker = ThreadStub()
    context = ContextStub(close_error=OSError("device close failed"))
    install(monkeypatch, worker=worker, context=context)
    original = ValueError("server failed")

    def serve(*args, **kwargs):
        raise original

    monkeypatch.setattr(main.uvicorn, "run", serve)
    with pytest.raises(ValueError) as error:
        main.main(["--simulate"])
    assert error.value is original
    assert context.closed and worker.joined, "关闭设备失败也不能跳过已启动线程的 join"


def test_normal_exit_joins_even_when_context_close_fails(monkeypatch):
    worker = ThreadStub()
    failure = OSError("device close failed")
    context = ContextStub(close_error=failure)
    install(monkeypatch, worker=worker, context=context)
    assert main.main(["--simulate"]) == 3
    assert worker.joined


@pytest.mark.parametrize("stage", ["create", "register"])
def test_motion_setup_failure_closes_context(monkeypatch, stage):
    worker, context = ThreadStub(), ContextStub()
    calls = install(monkeypatch, worker=worker, context=context)

    def fail(*args, **kwargs):
        raise RuntimeError("motion setup failed")

    if stage == "create":
        monkeypatch.setattr(main.threading, "Thread", fail)
    else:
        monkeypatch.setattr(context, "register_motion_thread", fail)
    assert main.main(["--simulate"]) == 3
    assert context.closed and not calls and not worker.joined


@pytest.mark.parametrize("stage", ["startup", "service"])
@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(9)])
def test_process_interrupt_keeps_original_and_cleans_up(monkeypatch, stage, failure):
    worker = ThreadStub(failure=failure if stage == "startup" else None)
    context = ContextStub(close_error=OSError("secondary cleanup failure"))
    install(monkeypatch, worker=worker, context=context)

    def serve(*args, **kwargs):
        raise failure

    monkeypatch.setattr(main.uvicorn, "run", serve)
    with pytest.raises(type(failure)) as error:
        main.main(["--simulate"])
    assert error.value is failure
    assert context.closed
    assert worker.joined is (stage == "service")


def test_shutdown_timeout_returns_nonzero_and_retains_running_flag(monkeypatch):
    worker, context = ThreadStub(), ContextStub()
    install(monkeypatch, worker=worker, context=context)
    monkeypatch.setattr(worker, "is_alive", lambda: True)
    assert main.main(["--simulate"]) == 3
    assert context.closed and worker.joined and context.worker_running


def test_startup_failure_not_replaced_by_cleanup_failure(monkeypatch, capsys):
    worker = ThreadStub(failure=RuntimeError("original start failure"))
    context = ContextStub(close_error=TypeError("secondary close bug"))
    install(monkeypatch, worker=worker, context=context)
    assert main.main(["--simulate"]) == 3
    assert "original start failure" in capsys.readouterr().err
    assert context.closed and not worker.joined


def test_context_closes_other_resources_after_audio_close_error(tmp_path, monkeypatch):
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "toy.db")}),
                            simulate=True, root=TOY_ROOT)
    calls = []
    original_db_close, original_router_close = context.db.close_all, context._router.close

    def db_close():
        calls.append("db")
        original_db_close()

    def router_close():
        calls.append("queues")
        original_router_close()

    def audio_close():
        calls.append("audio")
        raise OSError("audio close failed")

    monkeypatch.setattr(context.db, "close_all", db_close)
    monkeypatch.setattr(context._router, "close", router_close)
    monkeypatch.setattr(context.player.backend, "close", audio_close)
    try:
        with pytest.raises(OSError, match="audio close failed"):
            context.close()
        assert calls == ["audio", "queues", "db"], "一个资源关闭失败不能阻止其他资源释放"
    finally:
        original_router_close()
        original_db_close()
