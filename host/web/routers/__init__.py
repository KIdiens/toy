"""API 路由包：每个模块一个资源族，只声明端点 + 请求模型 + 调用 ``AppContext``。

分层纪律：本包位于 ``web`` 层，**禁止**直接 import 仓储 / 写 SQL / 触碰线程；
一切数据读写与状态变更都经 ``AppContext``（``host.adapters.ports``）。

- ``dev_sim`` 仅在 SIM 档位被 ``app.create_app`` 挂载，生产不注册。
"""
from __future__ import annotations

__all__ = [
    "actions",
    "content",
    "dev_sim",
    "events",
    "features",
    "memories",
    "reminders",
    "roles",
    "settings",
    "status",
]
