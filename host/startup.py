"""入口的错误分类与退出码；线程收尾位于 lifecycle。"""
from __future__ import annotations

import logging
import sqlite3
import sys
from tomllib import TOMLDecodeError

from host.config.cli import describe_config_error as _describe_config_error

# 退出码契约（供 systemd / 容器 / CI 判别「配置错了」还是「运行时崩了」）。
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_RUNTIME_ERROR = 3

# 配置类异常：这些几乎总是「用户把某一项配置写错了」，
# 给一行中文「哪一项、怎么改」比抛英文 traceback 有用。
# 分母是显式白名单而非「所有异常」—— 见``_report_startup_failure`` 的误伤面说明。
_CONFIG_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,          # int()/float() 解析环境变量失败
    TOMLDecodeError,     # defaults.toml 语法错
    KeyError,            # 配置缺必需项
    FileNotFoundError,   # 配置文件不存在
    PermissionError,     # 路径不可写
    OSError,             # 含 sqlite3.OperationalError 之外的 IO 问题
)

# 运行时类异常里需要特别说明「这是环境/依赖问题」的那些。
_RUNTIME_HINTS: tuple[type[BaseException], ...] = (
    sqlite3.Error,       # 库打不开/ 建表失败
    OSError,             # 串口/ 设备 / 目录不可写
    ImportError,         # 依赖缺失
    RuntimeError,        # 典型是 threading.Thread.start() 的「can't start new thread」
                         # （线程/句柄/内存耗尽）—— 属资源环境问题。
                         # 代价：真正的编程 bug 若抛 RuntimeError 也会走这条路。
                         # 之所以可接受：log.exception 仍留全量 traceback、退出码非 0、
                         # 摘要里带阶段名；而 TypeError/AttributeError/NameError
                         # 这些最常见的编程错误**不在**本白名单，仍原样抛出。
)


def _report_startup_failure(exc: BaseException, *, phase: str) -> int:
    """统一汇报启动期失败：日志留全量 traceback，终端给一行中文 + 返回非 0 退出码。

    **误伤面（重要）**：这里绝不把异常吞掉。``log.exception`` 已把完整 traceback
    写进日志，终端那行中文只是**额外**的摘要，不是替代品。
    分界线是「异常类型」而非「是否像编程错误」：

    - 命中 ``_CONFIG_ERRORS`` / ``_RUNTIME_HINTS`` 白名单 → 判为环境/配置问题，
      给中文指引 + 对应退出码。ValueError/TOMLDecodeError 这类是数据问题，
      不是代码问题，中文指引不会掩盖任何真相。
    - 不命中（即TypeError / AttributeError / NameError 等**编程错误**）
      → 走 ``raise`` 原样抛出，保留 Python 原生 traceback 让人看堆栈。

    反过来说：如果哪天某个 TypeError 其实也想给中文提示，
    正确做法是修好它或加进白名单，**不要**把 except Exception 写宽。
    """
    if isinstance(exc, _CONFIG_ERRORS):
        code, hint = EXIT_CONFIG_ERROR, _describe_config_error(exc)
    elif isinstance(exc, _RUNTIME_HINTS):
        code = EXIT_RUNTIME_ERROR
        hint = (f"{phase}失败（{type(exc).__name__}: {exc}）。"
                "请检查 DB_PATH 指向的目录是否可写、串口是否被占用、依赖是否安装。")
    else:
        # 编程错误：保持原生 traceback，不伪装成「配置问题」。
        raise exc
    logging.getLogger("host.main").exception("启动期失败于%s", phase)
    print(f"启动失败[{phase}]：{hint}", file=sys.stderr)
    return code


def report_shutdown_failure(exc: BaseException) -> int:
    """收尾阶段的设备或线程错误归运行时失败，不误报配置错误。"""
    if not isinstance(exc, _RUNTIME_HINTS):
        raise exc
    logging.getLogger("host.main").error("服务收尾失败", exc_info=exc)
    print(f"收尾失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    return EXIT_RUNTIME_ERROR
