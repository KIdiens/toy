"""上下文与领域线程的收尾；每个已登记资源都有清理机会。"""
from __future__ import annotations

import sys
import threading
from contextlib import ExitStack

from host.core.errors import StorageError

MOTION_JOIN_TIMEOUT_S = 2.0


def close_context(context) -> None:
    with context._lock:
        if context.tool_performer is not None:
            context.tool_performer.cancel()
        context._shutdown.set()
        context._invalidate_plays()
        context.action_progress = None
    with ExitStack() as cleanup:
        cleanup.callback(_close_database, context)
        if context._router is not None:
            cleanup.callback(context._router.close)
        cleanup.callback(context.player.backend.close)
        cleanup.callback(context.bus.shutdown)
        cleanup.callback(_join_motion, context)
        context.stop_workers()


def _join_motion(context) -> None:
    thread = context._motion_thread
    if thread is not None and thread.ident is not None:
        if thread is threading.current_thread():
            raise RuntimeError("不能在运动线程自身执行上下文总清理")
        thread.join(timeout=MOTION_JOIN_TIMEOUT_S)
        if thread.is_alive():
            raise RuntimeError("运动线程在关停等待期内未退出")
    context.worker_running = False


def _close_database(context) -> None:
    motion = context._motion_thread
    if context._domain_threads or (motion is not None and motion.is_alive()):
        # 保留连接登记，待线程释放自身连接后可再次 close；不假装清理成功。
        if context.log is not None:
            context.log.warning("工作线程未退出，暂缓数据库总清理")
        return
    try:
        context.db.close_all()
    except StorageError:
        if context.log is not None:
            context.log.warning("关闭数据库连接时出错", exc_info=True)


def stop_domain_workers(context, *, timeout: float) -> None:
    if context._worker_stop is not None:
        context._worker_stop.set()
    threads = list(context._domain_threads)
    try:
        with ExitStack() as cleanup:
            for thread in threads:
                if thread.ident is not None and thread is not threading.current_thread():
                    cleanup.callback(thread.join, timeout=timeout)
            control = getattr(context, "audio_control", None)
            if control is not None:
                control.close()  # 先唤醒录放音，再执行 join 回调。
    finally:
        # 超时线程仍由上下文持有，不能清掉引用后宣称已停止。
        context._domain_threads[:] = [thread for thread in threads if thread.is_alive()]
        if context._domain_threads and sys.exc_info()[0] is None:
            names = ", ".join(thread.name for thread in context._domain_threads)
            raise RuntimeError(f"领域线程在 {timeout:g}s 内未退出：{names}")
