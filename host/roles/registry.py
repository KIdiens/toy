"""角色注册、加载、校验与切换。

- **切换即 ``session_generation += 1``**（AC-15）：经注入的 ``state_machine.switch_role()`` 完成，
  使旧角色的迟到 AI 回包一律失效。
- 只做「扫描 / 校验 / 索引 / 切换」；动作文件的读写走文件系统，动作执行走 core。
- 角色配置非法 → ``ConfigError``（不静默跳过，避免「以为加载了其实没有」）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from host.core.errors import ConfigError
from host.roles.rolepack import RESIDUE_SUFFIXES
from host.roles.schema import RoleConfig

ROLE_JSON = "role.json"
ACTIONS_DIR = "actions"


class RoleRegistry:
    """内存中的角色索引；``root`` 通常是 ``content/roles``。"""

    def __init__(self, root: str | Path, *, state_machine=None, logger=None) -> None:
        self.root = Path(root)
        self.state_machine = state_machine
        self.logger = logger
        self._roles: dict[int, RoleConfig] = {}
        self._active: int | None = None
        self._current_action: str | None = None

    # ---------- 加载 / 注册 ----------
    def load_all(self) -> list[RoleConfig]:
        self._roles.clear()
        if not self.root.is_dir():
            self._active = None
            return []
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if directory.name.endswith(RESIDUE_SUFFIXES) or directory.name.startswith("."):
                # 导入中途崩溃/回收失败会留下 `<role>.bak` / `<role>.incoming`。它们是残留
                # 而非角色：被当作角色加载会让同一 role_id 出现两个来源 → register 抛
                # ConfigError → **整个 load_all 失败、服务起不来**；版本号相同时更糟——
                # 服务正常起来但静默加载了错误配置。跳过它们同时也是对历史残留的自愈。
                continue
            config_path = directory / ROLE_JSON
            if not config_path.is_file():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                self.register(RoleConfig.model_validate(data))
            except (ValueError, TypeError) as exc:
                raise ConfigError(f"角色配置非法: {config_path} ({exc})") from exc
        if self._active is not None and self._active not in self._roles:
            self._active = None
        return self.list()

    def register(self, config: RoleConfig) -> None:
        existing = self._roles.get(config.role_id)
        if existing is not None and existing.config_version != config.config_version:
            raise ConfigError(
                f"角色 {config.role_id} 版本冲突: "
                f"{existing.config_version} != {config.config_version}"
            )
        self._roles[config.role_id] = config

    # ---------- 查询 ----------
    def list(self) -> list[RoleConfig]:
        return [self._roles[i] for i in sorted(self._roles)]

    def exists(self, role_id: int) -> bool:
        return role_id in self._roles

    def get(self, role_id: int) -> RoleConfig:
        config = self._roles.get(role_id)
        if config is None:
            raise ConfigError(f"未注册的角色: {role_id}")
        return config

    @property
    def active_role_id(self) -> int | None:
        return self._active

    def active(self) -> RoleConfig | None:
        return self._roles.get(self._active) if self._active is not None else None

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "role_id": c.role_id,
                "name": c.name,
                "config_version": c.config_version,
                "tts_voice": c.tts_voice,
                "language": c.language,
            }
            for c in self.list()
        ]

    # ---------- 切换 ----------
    def set_active(self, role_id: int, *, bump: bool = True) -> RoleConfig:
        """切换到指定角色；``bump`` 时使 ``session_generation += 1``。"""
        config = self.get(role_id)
        self._active = role_id
        self._current_action = None
        if bump and self.state_machine is not None:
            self.state_machine.switch_role()
        if self.logger is not None:
            self.logger.info("active role -> %s (%s)", role_id, config.config_version)
        return config

    # ---------- 动作白名单 / 文件 ----------
    def actions_for(self, role_id: int | None = None) -> list[str]:
        target = role_id if role_id is not None else self._active
        if target is None or target not in self._roles:
            return []
        return list(self._roles[target].actions)

    def action_path(self, role_id: int, action_id: str) -> Path | None:
        if action_id not in self.actions_for(role_id):
            return None
        path = self._role_dir(role_id) / ACTIONS_DIR / f"{action_id}.json"
        return path if path.is_file() else None

    def action_files(self, role_id: int | None = None) -> list[dict[str, Any]]:
        """列出该角色可编辑的动作文件；``active`` 为当前指针（默认首个）。"""
        target = role_id if role_id is not None else self._active
        if target is None or target not in self._roles:
            return []
        directory = self._role_dir(target) / ACTIONS_DIR
        names = sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
        if self._current_action in names:
            current = self._current_action
        else:
            current = names[0] if names else None
        return [{"name": name, "active": name == current} for name in names]

    def set_current_action(self, name: str) -> None:
        self._current_action = name

    def _role_dir(self, role_id: int) -> Path:
        return self.root / str(role_id)
