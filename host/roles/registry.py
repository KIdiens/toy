"""角色注册、加载、校验与切换。

- **切换即 ``session_generation += 1``**（AC-15）：经注入的 ``state_machine.switch_role()`` 完成，
  使旧角色的迟到 AI 回包一律失效。
- 只做「扫描 / 校验 / 索引 / 切换」；动作文件的读写走文件系统，动作执行走 core。
- 角色配置非法 → ``ConfigError``（不静默跳过，避免「以为加载了其实没有」）。
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from host.core.errors import ConfigError
from host.roles.rolepack import RESIDUE_SUFFIXES
from host.roles.schema import PersonaConfig, RoleConfig

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
                # 人设随摘要一起给出：网页界面打开时要知道「现在是什么性格」，
                # 否则编辑页只能靠 PATCH 回包猜现状。``None`` 表示该角色没填人设。
                "persona": c.persona.model_dump() if c.persona else None,
            }
            for c in self.list()
        ]

    # ---------- 就地改人设（同一份角色包被编辑，不涉及来源竞争）----------
    def save_persona(self, role_id: int, persona: PersonaConfig | None) -> RoleConfig:
        """把**已校验**的人设写回该角色的 ``role.json``，并只重载这一个角色。

        - 入参只接受 ``PersonaConfig | None``（``None`` = 清空）。web 层负责先校验再
          调这里，因此落到磁盘的永远是 ``model_dump()`` 的值，不是用户原文 ——
          「绝不写未校验文本到磁盘」这条约束的唯一落点。
        - 路径只由 int 拼（``_role_dir``），不接受任何用户字符串，杜绝路径穿越。
        - 原子写：同目录临时文件 + ``os.replace``。中途失败（磁盘满/只读）不会留下
          半个 JSON —— 半写会让服务**下次启动直接起不来**。
        - ``OSError`` 包成 ``ConfigError``（→ HostError → 500 信封），消息带真实原因：
          吞掉或改写成「服务内部错误」会让用户以为是自己填的内容有问题。
        """
        path = self._role_dir(role_id) / ROLE_JSON
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"读取角色配置失败: {path} ({exc})") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"角色配置不是对象: {path}")

        # 只覆盖 persona 一个键：限位/版本/动作白名单等其他字段原样保留。
        data["persona"] = None if persona is None else persona.model_dump()
        self._write_json_atomic(path, data)
        return self.reload_one(role_id)

    def reload_one(self, role_id: int) -> RoleConfig:
        """重读单个角色的 ``role.json`` 并**就地替换**内存配置（绕开版本守卫）。

        为什么**绕开** ``register`` 的版本冲突守卫：那守卫处理的是「同一 role_id 出现
        两个来源」（如残留目录 + 正式目录），需要人裁定谁对；这里只有一个来源 ——
        同一份角色包被网页就地编辑了一行，没有可判定的冲突对象。

        为什么**不调** ``load_all``：它会先 ``clear()`` 再逐个加载，于是「另一个角色的
        文件写坏了」会变成**本次保存**的失败原因，而且失败时注册表已经空了（服务等于
        当场失忆）。改人设的影响面必须只有 role_id 这一个角色。
        """
        config_path = self._role_dir(role_id) / ROLE_JSON
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            config = RoleConfig.model_validate(data)
        except (OSError, ValueError, TypeError) as exc:
            raise ConfigError(f"角色配置非法: {config_path} ({exc})") from exc
        self._roles[role_id] = config
        return config

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        """临时文件 + ``os.replace``：读者要么看到旧内容，要么看到新内容。"""
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_name: str | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".role-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            tmp_name = None  # 已被 replace 消费，不再需要清理
        except OSError as exc:
            raise ConfigError(f"写入角色配置失败: {path} ({exc})") from exc
        finally:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)  # 清理失败不掩盖原始错误

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
