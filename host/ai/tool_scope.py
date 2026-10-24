"""每轮工具的会话守卫与待执行效果；只传递数据，不在此操作设备。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from host.core.errors import AiCancelled, ToolValidationError

MAX_EFFECTS = 8
_request: ContextVar[tuple[Callable[[], bool], dict] | None] = ContextVar(
    'tool_request', default=None)
_effects: ContextVar[list | None] = ContextVar('tool_effects', default=None)


@contextmanager
def request_scope(guard: Callable[[], bool], metadata: dict) -> Iterator[None]:
    token = _request.set((guard, metadata))
    try:
        yield
    finally:
        _request.reset(token)


def request_metadata() -> dict | None:
    request = _request.get()
    return request[1] if request is not None else None


def check_request() -> None:
    request = _request.get()
    if request is not None and not request[0]():
        raise AiCancelled('本轮已取消或角色已变化，停止后续请求')


@contextmanager
def collect_effects() -> Iterator[list]:
    effects: list = []
    token = _effects.set(effects)
    try:
        yield effects
    finally:
        _effects.reset(token)


def defer_effect(effect: dict[str, Any]) -> None:
    check_request()
    effects = _effects.get()
    if effects is None:
        raise ToolValidationError('播放工具须在一轮 AI 回复中调用')
    if len(effects) >= MAX_EFFECTS:
        raise ToolValidationError(f'一轮最多安排 {MAX_EFFECTS} 个播放任务，请分次请求')
    effects.append(deepcopy(effect))


def has_pending_media() -> bool:
    return any(isinstance(effect, dict) and effect.get('kind') in ('action', 'content')
               for effect in (_effects.get() or ()))
