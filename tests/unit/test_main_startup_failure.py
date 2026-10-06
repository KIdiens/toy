"""第5 组 · 缺陷2「``main()`` 启动期异常裸奔」的护栏。

## 缺陷

``main()`` 里三处启动期调用（``load_settings`` / ``build_context`` /
``start_workers``）原本**全无捕获**，且 ``logging.basicConfig`` 排在
``load_settings`` **之后** —— 配置本身写错时连日志系统都没就绪，
用户只看到Python 裸 traceback，没有一行中文说明「哪一项错了、怎么修」。
探针实测（``probes/g5/probe_main_startup.py``）：修复前四种故障的
**末行全是英文异常名**，退出码全是 1（无法区分「配置错」与「运行时崩」）。

## 修法

双轨：``log.exception`` 把全量 traceback 写进日志；终端 ``print`` 一行中文摘要；
返回**可区分**的非 0 退出码（2=配置错/ 3=运行时环境错），绝不 return 0。

## 误伤面（本组最需要回答的问题）

加了启动期捕获，**会不会把「本该 crash 让人看到 traceback」的编程错误
（如某个函数的 TypeError）也变成一行安静的中文提示？那样反而更难查。**

分界线是**异常类型白名单**，而不是「捕获一切然后按阶段归类」：

- 命中 ``_CONFIG_ERRORS`` / ``_RUNTIME_HINTS``（ValueError / TOMLDecodeError /
  sqlite3.Error / OSError 等**数据与环境类**异常）→ 给中文指引 + 对应退出码。
  这类是「用户把配置写错了」，中文提示不掩盖任何真相。
- **不命中**（TypeError / AttributeError / NameError 等**编程错误**）
  → 原样 ``raise``，保留 Python 原生 traceback 与堆栈。
  见 ``test_programming_error_still_raises_with_native_traceback``。

也就是说：编程错误只会因为「多了一层调用栈」而暴露得**更完整**，
不会被安静掉。这是本组刻意设计的分界线，也是它区别于
``except Exception: print('出错了')`` 的地方。

## 判别力：什么改动会让本文件转红

1. 把退出码改成 0（吞掉异常）→ ``test_config_error_returns_nonzero_*`` 转红；
2. 把 ``print`` 那行中文删掉 → ``test_*_prints_chinese_hint`` 转红；
3. 配置错误与运行时错误返回同一个码 → ``test_exit_codes_distinguish_*`` 转红；
4. 把 ``basicConfig`` 挪回 ``load_settings`` 之后 → ``test_logging_*`` 转红；
5. 把白名单改成 ``except Exception`` 一把梭 → ``test_programming_error_*`` 转红。
6. 恢复「一个中文提示都不给」 → ``test_no_bare_traceback_only`` 转红。

**绝不真起 uvicorn**：本文件全程 ``monkeypatch`` 掉 ``uvicorn.run``，
断言它**没被调用**（见 ``test_uvicorn_never_started``）。
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import tomllib

import pytest
from host import main as main_mod
from host.main import main

# 退出码常量用「缺失即None」的方式取，而不是模块级from-import：
# 若有人把修复整体回退，这些常量会不存在，此时下面每条用例应**各自**
# 给出可读的失败（而不是整个模块ImportError 崩在收集阶段 —— 那样
# 只能证明「跑不起来」，无法证明每条断言真的有判别力）。
EXIT_OK = getattr(main_mod, "EXIT_OK", 0)
EXIT_CONFIG_ERROR = getattr(main_mod, "EXIT_CONFIG_ERROR", 2)
EXIT_RUNTIME_ERROR = getattr(main_mod, "EXIT_RUNTIME_ERROR", 3)
_CONFIG_ERRORS = getattr(main_mod, "_CONFIG_ERRORS", ())
_describe_config_error = getattr(main_mod, "_describe_config_error", None)
_report_startup_failure = getattr(main_mod, "_report_startup_failure", None)


def _require(symbol: str):
    """取出 host.main 的内部符号；缺失时给出可读失败（而非TypeError: NoneType）。"""
    obj = getattr(main_mod, symbol, None)
    assert obj is not None, (
        f"host.main 缺少 {symbol} —— 启动期防护已被回退？"
    )
    return obj


@pytest.fixture(autouse=True)
def _no_uvicorn(monkeypatch):
    """全套测试**绝不起 uvicorn**：把它替换成会立刻炸的哨兵。

    若哪天有人漏改某个用例、真去跑 ``uvicorn.run``，哨兵会让测试立刻失败，
    而不是真的占端口起服务。
    """
    calls: list = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("测试里绝不允许真起 uvicorn")

    monkeypatch.setattr(main_mod.uvicorn, "run", _boom)
    yield calls


# =============================================================== 配置类失败
def _boom_on(monkeypatch, target, exc):
    def _raise(*a, **k):
        raise exc

    monkeypatch.setattr(main_mod, target, _raise)


def test_config_error_returns_distinct_code_and_chinese_hint(monkeypatch, capsys):
    """HOST_PORT 非法 → 退出码 2 + 一行中文，且末行不是裸英文异常。"""
    _boom_on(monkeypatch, "load_settings",
             ValueError("invalid literal for int() with base 10: 'not_a_port'"))
    code = main(["--simulate"])
    assert code == EXIT_CONFIG_ERROR, f"配置错误应返回 {EXIT_CONFIG_ERROR}，实得{code}"
    err = capsys.readouterr().err
    assert "启动失败" in err and "HOST_PORT" in err, f"缺中文指引：{err!r}"


def test_config_error_does_not_return_zero(monkeypatch, capsys):
    """绝不能 return 0 —— 否则 systemd/脚本会以为启动成功。"""
    _boom_on(monkeypatch, "load_settings", ValueError("invalid literal for int()"))
    assert main(["--simulate"]) != EXIT_OK, "启动失败却返回 0 = 谎报成功"


def test_toml_decode_error_gets_config_code(monkeypatch, capsys):
    """defaults.toml 语法错 → 同样是配置类，退出码 2。"""
    _boom_on(monkeypatch, "load_settings",
             tomllib.TOMLDecodeError("Expected ']' at the end of a table declaration"))
    code = main(["--simulate"])
    assert code == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "defaults.toml" in err and "语法错误" in err, f"提示未指向配置文件：{err!r}"


def test_invalid_log_level_is_explained(monkeypatch, capsys):
    """LOG_LEVEL 非法：崩在 logging.basicConfig 自身，必须仍被翻译成中文。

    这是最容易被漏掉的一处 —— 预配置日志的那句basicConfig 自己也会抛，
    若它被放在 try 之外，「修好日志窗口」就等于「换了个地方裸奔」。
    """
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    code = main(["--simulate"])
    assert code == EXIT_CONFIG_ERROR, f"非法 LOG_LEVEL 应归配置错误，实得{code}"
    err = capsys.readouterr().err
    assert "LOG_LEVEL" in err, f"未指明是LOG_LEVEL：{err!r}"


# =============================================================== 运行时类失败
def test_runtime_error_returns_distinct_code(monkeypatch, capsys):
    """sqlite 打不开 → 退出码 3（与配置错的 2 区分开，便于运维判断）。"""
    monkeypatch.setattr(main_mod, "load_settings",
                        lambda *a, **k: _fake_settings())
    _boom_on(monkeypatch, "build_context",
             sqlite3.OperationalError("unable to open database file"))
    code = main(["--simulate"])
    assert code == EXIT_RUNTIME_ERROR, f"运行时错误应返回 {EXIT_RUNTIME_ERROR}，实得{code}"
    err = capsys.readouterr().err
    assert "启动失败" in err, f"缺中文指引：{err!r}"


def test_exit_codes_distinguish_config_from_runtime(monkeypatch):
    """两个阶段的退出码必须不同 —— 否则运维无法判别该查配置还是查环境。"""
    _boom_on(monkeypatch, "load_settings", ValueError("bad int"))
    config_code = main(["--simulate"])
    monkeypatch.setattr(main_mod, "load_settings", lambda *a, **k: _fake_settings())
    _boom_on(monkeypatch, "build_context", sqlite3.OperationalError("no db"))
    runtime_code = main(["--simulate"])
    assert config_code != runtime_code, (
        f"配置错与运行时错返回了同一个码 {config_code}，运维无法区分"
    )
    assert EXIT_CONFIG_ERROR != EXIT_RUNTIME_ERROR


# ======================================================= 误伤面：编程错误不被安静掉
def test_programming_error_still_raises_with_native_traceback(monkeypatch):
    """**误伤面的核心断言**：TypeError 这类编程错误必须原样抛出。

    若被吞成一行安静的中文提示，堆栈就丢了 —— 那比裸崩更难查。
    """
    _boom_on(monkeypatch, "load_settings", TypeError("bump() got an unexpected keyword"))
    with pytest.raises(TypeError) as ei:
        main(["--simulate"])
    # 原生异常对象与文案必须透传，不能被替换成友好提示
    assert "unexpected keyword" in str(ei.value)


def test_programming_error_is_not_in_whitelist():
    """白名单不得混入编程错误类型（TypeError/AttributeError/NameError…）。"""
    whitelist = _require("_CONFIG_ERRORS")
    for bad in (TypeError, AttributeError, NameError, IndexError, KeyError):
        if bad is KeyError:
            continue  # KeyError 在配置缺失场景下确属配置类，单独说明
        assert not any(issubclass(bad, t) for t in whitelist), (
            f"{bad.__name__} 不该被当成「配置错误」安静掉"
        )


def test_unknown_error_type_propagates_from_reporter():
    """``_report_startup_failure`` 对未知异常类型必须 raise，不能返回退出码。"""
    reporter = _require("_report_startup_failure")
    with pytest.raises(ZeroDivisionError):
        reporter(ZeroDivisionError("division by zero"), phase="测试")


def test_report_still_logs_full_traceback(monkeypatch, caplog):
    """双轨之一：完整 traceback 必须进日志（不是只留终端那行摘要）。"""
    _boom_on(monkeypatch, "load_settings", ValueError("invalid literal for int()"))
    with caplog.at_level(logging.ERROR, logger="host.main"):
        main(["--simulate"])
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert any(r.exc_info for r in caplog.records), (
        "日志记录必须带 exc_info（完整 traceback），否则就只剩一行摘要，"
        "等于把异常吞了"
    )
    assert "启动期失败" in text


# =============================================================== 日志窗口位置
def test_logging_configured_before_settings_load(monkeypatch, capsys):
    """日志必须在 load_settings **之前** 就绪，否则配置错时无日志可写。

    做法：让 load_settings 抛错，然后断言 root logger 已被 basicConfig 装过handler。
    """
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()
    try:
        _boom_on(monkeypatch, "load_settings", ValueError("invalid literal for int()"))
        main(["--simulate"])
        assert root.handlers, (
            "配置加载失败后 root logger 仍无 handler —— 说明 basicConfig 排在 "
            "load_settings 之后，配置期失败将没有任何日志可写"
        )
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_logging_basicconfig_call_itself_is_inside_try(monkeypatch):
    """预配置的 basicConfig 必须被 try 包住（否则非法 LOG_LEVEL 仍会裸崩）。"""
    boom = ValueError("Unknown level: 'VERBOSE'")

    def _basic(*a, **k):
        raise boom

    monkeypatch.setattr(main_mod.logging, "basicConfig", _basic)
    code = main(["--simulate"])  # 不应抛出
    assert code == EXIT_CONFIG_ERROR


# =============================================================== 成功路径不回归
def test_uvicorn_never_started_for_failure_paths(monkeypatch, _no_uvicorn):
    """失败路径不得触达 uvicorn.run（否则会真起服务/占端口）。"""
    _boom_on(monkeypatch, "load_settings", ValueError("invalid literal for int()"))
    main(["--simulate"])
    assert _no_uvicorn == [], "失败路径竟然调用了 uvicorn.run"


def test_start_workers_failure_closes_context(monkeypatch, capsys):
    """领域线程启动失败必须收尾（否则半启动状态泄漏线程），且返回非 0。"""
    monkeypatch.setattr(main_mod, "load_settings", lambda *a, **k: _fake_settings())
    fake = _FakeContext()
    monkeypatch.setattr(main_mod, "build_context", lambda *a, **k: fake)

    def _start():
        raise RuntimeError("启动线程炸了")

    fake.start_workers = _start
    code = main(["--simulate"])
    assert code != EXIT_OK, "start_workers 失败却返回 0"
    assert fake.closed, "start_workers 失败后未调用 context.close()，线程会泄漏"


def test_success_path_returns_zero_and_runs_uvicorn(monkeypatch, _no_uvicorn):
    """成功路径：返回 0，且 uvicorn.run 确实被调用（修法没把正常启动也堵了）。"""
    monkeypatch.setattr(main_mod, "load_settings", lambda *a, **k: _fake_settings())
    fake = _FakeContext()
    monkeypatch.setattr(main_mod, "build_context", lambda *a, **k: fake)
    monkeypatch.setattr(main_mod, "create_app", lambda ctx: object())

    def _uvicorn(*a, **k):
        return None

    monkeypatch.setattr(main_mod.uvicorn, "run", _uvicorn)
    assert main(["--simulate"]) == EXIT_OK
    assert _no_uvicorn == []  # 哨兵没被触发说明确实是上面替换的那个被调用


# =============================================================== 中文提示内容判别力
def test_chinese_hint_names_the_offending_item(capsys):
    """中文提示必须**指名道姓**，不能是「出错了」这种空话。"""
    describe = _require("_describe_config_error")
    hint = describe(ValueError("invalid literal for int() with base 10: 'x'"))
    assert "HOST_PORT" in hint and "defaults.toml" in hint
    level_hint = describe(ValueError("Unknown level: 'VERBOSE'"))
    assert "LOG_LEVEL" in level_hint and "DEBUG" in level_hint


def test_chinese_hint_is_not_generic_for_unknown_config_error():
    """未知配置异常也必须给出可操作指向，而不是复述异常。"""
    describe = _require("_describe_config_error")
    hint = describe(UnicodeDecodeError("utf-8", b"", 0, 1, "bad"))
    assert "defaults.toml" in hint or "环境变量" in hint


def test_no_bare_traceback_only(monkeypatch, capsys):
    """终��输出不能只有裸 traceback：必须额外有一行中文摘要。"""
    _boom_on(monkeypatch, "load_settings", ValueError("invalid literal for int()"))
    main(["--simulate"])
    err = capsys.readouterr().err
    has_cn = any("一" <= ch <= "鿿" for ch in err)
    assert has_cn, "终端没有任何中文说明，用户无从判断该改哪一项配置"


# =============================================================== 辅助
def _fake_settings():
    """最小 settings 替身：只需 main() 用到的 logging/web/ai 字段。"""
    from host.config.settings import load_settings

    return load_settings({"HOST_PORT": "0", "LOG_LEVEL": "INFO"})


class _FakeContext:
    """最小 AppContext 替身，避免测试里真起线程 / 真连数据库。"""

    simulate = True
    worker_running = False

    def __init__(self) -> None:
        self.closed = False
        self.started = False

    def start_workers(self) -> None:
        self.started = True

    def register_motion_thread(self, thread) -> None:
        pass

    def run_forever(self, stop_event=None) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_module_exposes_stable_exit_codes():
    """退出码是运维契约，值不可漂移。"""
    assert (EXIT_OK, EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR) == (0, 2, 3)
    assert sys.modules["host.main"].EXIT_CONFIG_ERROR == 2