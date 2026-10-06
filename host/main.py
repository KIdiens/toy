"""主机入口：装配 + 启动（**只装配，不含业务逻辑**）。

用法::

    python -m host.main --simulate      # 无硬件档位（进程内 MCU 模拟器）
    python -m host.main                 # 串口真机档位

可用 ``--host`` / ``--port`` / ``--db`` / ``--log-level`` 覆盖默认配置。
线程模型：1 个 worker 线程执行 ``AppContext.run_forever``，是运动队列与事件总线的唯一消费者。
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import threading
from tomllib import TOMLDecodeError

import uvicorn

from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
WORKER_JOIN_TIMEOUT_S = 2.0

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


def _describe_config_error(exc: BaseException) -> str:
    """把配置异常翻译成「哪一项错了、怎么改」的中文一行。"""
    text = str(exc)
    if isinstance(exc, ValueError) and "invalid literal for int()" in text:
        return (
            f"配置项的取值不是合法数字（{text}）。"
            "请检查 HOST_PORT / MOTION_BAUD 等环境变量，或 host/config/defaults.toml 里的对应值。"
        )
    if isinstance(exc, ValueError) and "Unknown level" in text:
        return (
            f"LOG_LEVEL 取值非法（{text}）。"
            "请改成 DEBUG / INFO / WARNING / ERROR / CRITICAL 之一。"
        )
    if isinstance(exc, TOMLDecodeError):
        return (
            f"配置文件 host/config/defaults.toml 语法错误（{text}）。"
            "请检查 TOML 方括号是否成对、是否有中文标点。"
        )
    if isinstance(exc, FileNotFoundError):
        return f"找不到文件（{text}）。请检查配置路径或当前工作目录。"
    if isinstance(exc, PermissionError):
        return f"没有权限（{text}）。请检查 DB_PATH 指向的目录是否可写。"
    return (f"配置无法加载（{type(exc).__name__}: {text}）。"
            "请检查环境变量与 host/config/defaults.toml。")


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


def main(argv: list[str] | None = None) -> int:
    """装配并启动服务；返回进程退出码。

    退出码：0 正常 / 2 配置错误 / 3 运行时环境错误。
    """
    args = parse_args(argv)
    logger = logging.getLogger("host.main")

    # 日志必须**先于**配置加载配好：否则配置本身写错时（load_settings 抛异常）
    # 连日志系统都没就绪，用户只能看到裸 traceback，连「错在哪一项」都不知道。
    # 这一句自己也会因非法level 抛 ValueError（实测 LOG_LEVEL=VERBOSE），
    # 所以它必须在 try 内 —— 否则只是把「没日志的崩溃」换了个位置。
    try:
        logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                            format=LOG_FORMAT)
        settings = load_settings(build_env(args))
        # 配置里的 level 此时才可信，用它覆盖预配置（force=True 允许二次 basicConfig）。
        # **必须在同一个 try 内**：当 root logger 已有 handler 时，上面那句
        # basicConfig(force=False) 是 no-op、连level 都不校验，非法 LOG_LEVEL
        # 会推迟到这句force=True 才抛 —— 放外面就又裸奔了（实测 pytest 下复现）。
        logging.basicConfig(level=settings.logging.level.upper(), format=LOG_FORMAT, force=True)
    except Exception as exc:  # noqa: BLE001  启动期兜底：转成可运维的退出码
        return _report_startup_failure(exc, phase="配置加载")

    try:
        context = build_context(settings, simulate=args.simulate)
    except Exception as exc:  # noqa: BLE001  同上
        return _report_startup_failure(exc, phase="运行时装配")

    stop_event = threading.Event()
    context.worker_running = True
    # 领域线程（net / audio / tick）必须与状态机 worker 一同装配，否则 NET_REQUEST 等
    # 命令无人消费 → 按键对话卡在 THINKING（P0-1）。
    try:
        context.start_workers()
    except Exception as exc:  # noqa: BLE001  同上
        context.close()  # 半启动状态必须收尾，否则领域线程泄漏
        return _report_startup_failure(exc, phase="启动领域线程")
    worker = threading.Thread(target=context.run_forever, args=(stop_event,),
                              name="host-worker", daemon=True)
    # 必须登记引用：存活探针（context.worker_alive）靠 is_alive() 判活，
    # 拿不到引用就只能退回心跳判定 —— 而心跳在同步播放期间不更新，
    # 会把"正在播放"误报成"motion worker 已死"。
    context.register_motion_thread(worker)
    worker.start()
    logger.info("主机服务就绪：http://%s:%s（simulate=%s，AI=%s）",
                settings.web.host, settings.web.port, context.simulate, settings.ai.provider)
    try:
        uvicorn.run(create_app(context), host=settings.web.host, port=settings.web.port,
                    log_level=settings.logging.level.lower())
    finally:
        logger.info("主机服务停止，开始收尾")
        stop_event.set()
        context.close()  # 内含 stop_workers()：停领域线程并 join
        worker.join(timeout=WORKER_JOIN_TIMEOUT_S)
    return EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """命令行参数（不读全局状态，便于测试）。"""
    parser = argparse.ArgumentParser(prog="host.main", description="AI 角色玩具主机服务")
    parser.add_argument("--simulate", action="store_true", help="无硬件档位（进程内 MCU 模拟器）")
    parser.add_argument("--host", default=None, help="监听地址（覆盖 HOST_BIND）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（覆盖 HOST_PORT）")
    parser.add_argument("--db", default=None, help="SQLite 路径（覆盖 DB_PATH）")
    parser.add_argument("--log-level", default=None, help="日志级别（覆盖 LOG_LEVEL）")
    return parser.parse_args(argv)


def build_env(args: argparse.Namespace, base: dict[str, str] | None = None) -> dict[str, str]:
    """把命令行覆盖项折成配置层认识的环境变量。"""
    env = dict(os.environ if base is None else base)
    if args.host:
        env["HOST_BIND"] = args.host
    if args.port:
        env["HOST_PORT"] = str(args.port)
    if args.db:
        env["DB_PATH"] = args.db
    if args.log_level:
        env["LOG_LEVEL"] = args.log_level
    return env


if __name__ == "__main__":
    raise SystemExit(main())
