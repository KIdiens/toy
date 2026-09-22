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
import threading

import uvicorn

from host.adapters.runtime import build_context
from host.config.settings import load_settings
from host.web.app import create_app

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


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


def main(argv: list[str] | None = None) -> int:
    """装配并启动服务；返回进程退出码。"""
    args = parse_args(argv)
    settings = load_settings(build_env(args))
    logging.basicConfig(level=settings.logging.level.upper(), format=LOG_FORMAT)
    logger = logging.getLogger("host.main")

    context = build_context(settings, simulate=args.simulate)
    stop_event = threading.Event()
    context.worker_running = True
    worker = threading.Thread(target=context.run_forever, args=(stop_event,),
                              name="host-worker", daemon=True)
    worker.start()
    logger.info("主机服务就绪：http://%s:%s（simulate=%s，AI=%s）",
                settings.web.host, settings.web.port, context.simulate, settings.ai.provider)
    try:
        uvicorn.run(create_app(context), host=settings.web.host, port=settings.web.port,
                    log_level=settings.logging.level.lower())
    finally:
        logger.info("主机服务停止，开始收尾")
        stop_event.set()
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
