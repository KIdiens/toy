"""``play_content`` / ``play_action`` 工具：一切经**白名单**放行（§13）。

- ``play_content`` 只允许播放已收录的本地内容（``ContentCatalog``）；未收录明确报错。
- ``play_action`` 只允许当前角色白名单内的动作 ID；越权/不存在一律拒绝，绝不执行任意动作。
- 只有实际执行端口接受后才返回待执行；动作和内容均在安全回复结束后串行执行。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from host.ai.tools import ParamSpec, ToolRegistry
from host.audio.catalog import ContentCatalog
from host.core.errors import ToolValidationError

ActionIds = Callable[[], Sequence[str]]
PlayContent = Callable[[str], Any]


def register(
    registry: ToolRegistry,
    *,
    catalog: ContentCatalog,
    action_ids: ActionIds,
    play_content: PlayContent | None = None,
    play_action: PlayContent | None = None,
) -> None:
    """注册两个白名单工具；``action_ids`` 返回当前角色允许的动作。"""

    def content_handler(args: dict[str, Any]) -> dict[str, Any]:
        item = catalog.get(str(args["item_id"]))
        if item is None:
            raise ToolValidationError(f"内容不存在: {args['item_id']}")
        if play_content is None:
            raise ToolValidationError('本地内容执行端口未装配')
        return play_content(item.id)

    def action_handler(args: dict[str, Any]) -> dict[str, Any]:
        action_id = str(args["action_id"])
        allowed = list(action_ids())
        if action_id not in allowed:
            raise ToolValidationError(f"非白名单动作: {action_id}")
        if play_action is None:
            raise ToolValidationError('动作执行端口未装配')
        return play_action(action_id)

    registry.register(
        "play_content",
        "播放一个已收录的本地故事或音效。",
        (ParamSpec("item_id", str, required=True, pattern=r"[A-Za-z0-9_:\-]{1,60}"),),
        content_handler,
    )
    registry.register(
        "play_action",
        "播放当前角色白名单内的一个动作。",
        (ParamSpec("action_id", str, required=True, pattern=r"[A-Za-z0-9_\-]{1,40}"),),
        action_handler,
    )
