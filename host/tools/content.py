"""``play_content`` / ``play_action`` 工具：一切经**白名单**放行（§13）。

- ``play_content`` 只允许播放已收录的本地内容（``ContentCatalog``）；未收录明确报错。
- ``play_action`` 只允许当前角色白名单内的动作 ID；越权/不存在一律拒绝，绝不执行任意动作。
- 实际播放/运动由上层端口执行（本模块只做白名单校验与派发标记）。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from host.ai.tools import ParamSpec, ToolRegistry
from host.audio.catalog import ContentCatalog

ActionIds = Callable[[], Sequence[str]]
PlayContent = Callable[[str], Any]


def register(
    registry: ToolRegistry,
    *,
    catalog: ContentCatalog,
    action_ids: ActionIds,
    play_content: PlayContent | None = None,
) -> None:
    """注册两个白名单工具；``action_ids`` 返回当前角色允许的动作。"""

    def content_handler(args: dict[str, Any]) -> dict[str, Any]:
        item = catalog.get(str(args["item_id"]))
        if item is None:
            return {"ok": False, "error": f"内容不存在: {args['item_id']}"}
        if play_content is not None:
            play_content(item.id)
        return {"ok": True, "item_id": item.id, "kind": item.kind, "duration_s": item.duration_s}

    def action_handler(args: dict[str, Any]) -> dict[str, Any]:
        action_id = str(args["action_id"])
        allowed = list(action_ids())
        if action_id not in allowed:
            return {"ok": False, "error": f"非白名单动作: {action_id}"}
        return {"ok": True, "action_id": action_id, "scheduled": True}

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
