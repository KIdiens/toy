"""已装配运行时的统一收尾，清理失败不阻止其余资源释放。"""
from __future__ import annotations

import logging


def shutdown_runtime(context, worker, stop_event, *, timeout: float) -> BaseException | None:
    """返回首个收尾异常，由入口决定是否已有更早的异常需要保留。"""
    log = logging.getLogger("host.main")
    log.info("主机服务停止，开始收尾")
    stop_event.set()
    error = None
    try:
        context.close()
    except BaseException as exc:  # 即使设备关闭被中断，也必须尝试回收线程
        log.error("上下文收尾失败", exc_info=True)
        error = exc
    try:
        # Thread.start 之前失败时不能 join；start 抛错但线程已启动时仍须收尾。
        if worker is not None and worker.ident is not None:
            worker.join(timeout=timeout)
            if worker.is_alive():
                raise RuntimeError(f"运动线程在 {timeout:g}s 内未退出")
        context.worker_running = False
    except BaseException as exc:
        log.error("运动线程收尾失败", exc_info=True)
        if error is None:
            error = exc
    return error
