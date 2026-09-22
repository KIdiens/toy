"""Web 层：FastAPI 装配 + 薄路由（只编排，不含业务逻辑）。

- ``schemas`` 是出入参的**唯一定义**；``deps`` 提供上下文注入；
- ``routers`` 下每个模块只声明端点与请求校验，业务经 ``AppContext`` 完成；
- ``app`` 只装配（中间件 + 错误映射 + 挂载），零业务。
"""
from __future__ import annotations

__all__ = ["deps", "routers", "schemas"]
