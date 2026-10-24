"""主机入口：装配 + 启动（**只装配，不含业务逻辑**）。

用法::

    python -m host.main --simulate      # 无硬件档位（进程内 MCU 模拟器）
    python -m host.main --simulate --audio sd  # 模拟 MCU，使用真实声卡
    python -m host.main                 # 串口真机档位

可用 ``--host`` / ``--port`` / ``--db`` / ``--log-level`` 覆盖默认配置。
线程模型：1 个 worker 线程执行 ``AppContext.run_forever``，是运动队列与事件总线的唯一消费者。
"""
from __future__ import annotations

import logging
import os
import threading

import uvicorn

from host.adapters.runtime import build_context
from host.config.cli import build_env, parse_args
from host.config.settings import load_settings
from host.lifecycle import shutdown_runtime
from host.startup import _CONFIG_ERRORS as _CONFIG_ERRORS
from host.startup import EXIT_CONFIG_ERROR as EXIT_CONFIG_ERROR
from host.startup import EXIT_OK, _report_startup_failure, report_shutdown_failure
from host.startup import EXIT_RUNTIME_ERROR as EXIT_RUNTIME_ERROR
from host.startup import _describe_config_error as _describe_config_error
from host.web.app import create_app

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
WORKER_JOIN_TIMEOUT_S = 2.0


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
    worker = None
    context.worker_running = True
    try:
        phase = "启动领域线程"
        try:
            context.start_workers()
            phase = "启动运动线程"
            worker = threading.Thread(target=context.run_forever, args=(stop_event,),
                                      name="host-worker", daemon=True)
            context.register_motion_thread(worker)
            worker.start()
        except Exception as exc:  # noqa: BLE001  半启动状态统一收尾
            return _report_startup_failure(exc, phase=phase)
        logger.info("主机服务就绪：http://%s:%s（simulate=%s，AI=%s，audio=%s）",
                    settings.web.host, settings.web.port, context.simulate, settings.ai.provider,
                    settings.audio.backend)
        uvicorn.run(create_app(context), host=settings.web.host, port=settings.web.port,
                    log_level=settings.logging.level.lower())
    finally:
        error = shutdown_runtime(context, worker, stop_event, timeout=WORKER_JOIN_TIMEOUT_S)
    if error is not None:
        return report_shutdown_failure(error)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
