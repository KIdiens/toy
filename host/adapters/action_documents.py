"""动作文件读取与编排草稿快照；调用方持上下文锁。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from host.core.errors import ConflictError


def _read(context, role_id: int, action_id: str) -> dict:
    if not action_id or any(part in action_id for part in ("/", "\\", "\x00")):
        raise ConflictError("动作 ID 不能包含路径")
    path = context.roles.action_path(role_id, action_id)
    if path is None:
        raise ConflictError(f"动作不在角色白名单中或文件不存在: {action_id}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConflictError("动作文件不可读，请检查文件内容") from exc


def _validate(context, action, role_id: int) -> None:
    verdict = context.validate_action(action)
    if not verdict["valid"]:
        raise ConflictError(f"动作文件或草稿非法: {verdict['errors']}")
    if not context._action_matches_role(action, role_id):
        raise ConflictError(f"动作的 role_id 与当前角色 {role_id} 不一致，拒绝播放")


def read_document(context, role_id: int, action_id: str) -> dict:
    with context._lock:
        if context.roles.active_role_id != role_id:
            raise ConflictError("角色已变化，请重新选择动作文件")
        action = _read(context, role_id, action_id)
        _validate(context, action, role_id)
        config = context.roles.active()
        return {"action_id": action_id, "config_version": config.config_version,
                "action": action, "limits": {"min_us": config.min_us, "max_us": config.max_us,
                                             "neutral_us": 1500, "max_speed_us_s": 300}}


def prepare_play_document(context, role_id, action_id, action_version, action) -> dict:
    config = context.roles.active()
    if action is not None and action_version is None:
        raise ConflictError("编排草稿须携带角色配置版本，请重新连接后下发")
    if action_version is not None and action_version != config.config_version:
        raise ConflictError("角色配置版本已变化，请重新读取角色与动作")
    # 有 action 时执行显式编排快照；省略时继续走磁盘动作白名单，AI 工具不接受草稿。
    document = deepcopy(action) if action is not None else _read(context, role_id, action_id)
    _validate(context, document, role_id)
    return document
