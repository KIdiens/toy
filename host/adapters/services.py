"""适配层存储门面：把仓储读写包成**路由可直接调用的服务方法**（Mixin）。

- 路由**不直接** import 仓储或写 SQL；它们调用 ``AppContext``（本 Mixin 的方法）以获得
  出参字典。这样既满足「web 禁止直读数据库」，又保持路由极薄。
- 只做「读取 → DTO 转换 / 写入 → 事务」，**不含业务规则**（规则在 core/ai/tools）。
"""
from __future__ import annotations

from typing import Any

from host.adapters import dto
from host.core.errors import ClarifyError, HostError, RecordNotFound
from host.storage import memories_repo, profiles_repo, reminders_repo, settings_repo
from host.tools import reminders as reminder_tool

DEFAULT_PROFILE_NAME = "小朋友"
DEFAULT_TIMEZONE = "Asia/Shanghai"
# 前端与契约用 ``"default"`` 指代默认档案（见 host/web/static/pages/reminders.js）。
DEFAULT_PROFILE_ALIASES = frozenset({"default", "me", "self", "current"})

# SQLite 的 INTEGER 是 64 位有符号。越界整数会在驱动层抛**裸** ``OverflowError``
# （实测 19 位十进制数字即可触发）→ 纯文本 500，违反统一信封契约。
SQLITE_MAX_INT = 2**63 - 1


def _checked_id(raw: object, *, what: str) -> int:
    """把外部传入的编号安全转成 SQLite 可表示的整数；非法/越界一律转 422。"""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ClarifyError(f"非法{what}: {raw!r}") from exc
    if not (-SQLITE_MAX_INT - 1) <= value <= SQLITE_MAX_INT:
        raise ClarifyError(f"{what}超出可表示范围: {raw!r}")
    return value


class StorageFacade:
    """依赖宿主提供 ``db`` / ``clock`` / ``simulate`` / ``player``。"""

    def _resolve_profile(self, profile_id: str | int | None) -> int:
        if profile_id in (None, ""):
            return self.ensure_profile()
        text = str(profile_id).strip()
        if text.lower() in DEFAULT_PROFILE_ALIASES:
            return self.ensure_profile()
        return _checked_id(text, what="档案编号")

    def ensure_profile(self) -> int:
        existing = profiles_repo.list_all(self.db)
        if existing:
            return existing[0].id
        return profiles_repo.create(self.db, DEFAULT_PROFILE_NAME, now=self.clock.now_iso())

    # ---------- 记忆 ----------
    def list_memories(self, *, profile_id: str | int | None = None,
                      kind: str | None = None) -> list[dict[str, Any]]:
        pid = self._resolve_profile(profile_id)
        rows = memories_repo.list_by_profile(self.db, pid, kind=kind, include_expired=True)
        return [dto.memory_dto(m) for m in rows]

    def delete_memory(self, memory_id: str | int) -> dict[str, Any]:
        mid = _checked_id(memory_id, what="记忆编号")
        return {"deleted": memories_repo.delete(self.db, mid)}

    def clear_memories(self, *, profile_id: str | int | None = None) -> dict[str, Any]:
        pid = self._resolve_profile(profile_id)
        return {"removed": memories_repo.clear_profile(self.db, pid)}

    def memory_context(self, profile_id: int) -> list[str]:
        """供 AI 注入的已确认记忆文本；**调用方必须当作不可信数据封装**（D8）。"""
        rows = memories_repo.list_for_context(self.db, int(profile_id), now=self.clock.now_iso())
        return [f"{m.kind}: {m.value}" for m in rows]

    def add_memory(self, *, profile_id: int, kind: str, value: str,
                   confirmed: bool = True) -> dict[str, Any]:
        mid = memories_repo.add(self.db, int(profile_id), kind, value,
                                now=self.clock.now_iso(), confirmed=confirmed)
        return dto.memory_dto(memories_repo.get(self.db, mid))

    # ---------- 提醒 ----------
    def list_reminders(self, *, profile_id: str | int | None = None) -> list[dict[str, Any]]:
        pid = self._resolve_profile(profile_id)
        return [dto.reminder_dto(r) for r in reminders_repo.list_by_profile(self.db, pid)]

    def create_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        pid = self._resolve_profile(payload.get("profile_id"))
        idempotency_key = payload.get("idempotency_key")
        try:
            result = reminder_tool.create(
                self.db,
                text=str(payload["text"]),
                due_at_utc=str(payload["due_at_utc"]),
                now=self.clock.now_iso(),
                timezone=str(payload.get("timezone") or DEFAULT_TIMEZONE),
                profile_id=pid,
                repeat_rule=payload.get("repeat_rule"),
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            # `_normalize_due` 对非法时间抛裸 ValueError。这里转成 422 契约错误，
            # 而不是让它冒泡成纯文本 500。
            raise ClarifyError(f"提醒时间非法: {payload.get('due_at_utc')!r}（{exc}）") from exc
        if result.get("id"):
            return dto.reminder_dto(reminders_repo.get(self.db, result["id"]))
        # 幂等冲突：**只能**按幂等键取回同一条记录。绝不用「列表最后一条」冒充——
        # 那会把别人的提醒当成重放结果返回给客户端。
        if idempotency_key:
            existing = reminders_repo.get_by_idempotency_key(self.db, str(idempotency_key))
            if existing is not None:
                return dto.reminder_dto(existing)
        # 幂等冲突却找不到既有记录：属于真实的数据不一致，如实抛出而非伪造结果
        raise HostError("提醒创建未生效：幂等键冲突但未找到既有记录")

    def cancel_reminder(self, reminder_id: str | int) -> dict[str, Any]:
        rid = _checked_id(reminder_id, what="提醒编号")
        return {"cancelled": reminders_repo.cancel(self.db, rid)}

    def get_reminder(self, reminder_id: str | int) -> dict[str, Any]:
        rid = _checked_id(reminder_id, what="提醒编号")
        try:
            return dto.reminder_dto(reminders_repo.get(self.db, rid))
        except RecordNotFound as exc:
            raise ClarifyError(f"提醒不存在: {reminder_id}") from exc

    # ---------- 设置 / 开关 ----------
    def get_settings(self) -> dict[str, Any]:
        return dto.settings_dto(settings_repo.get_all(self.db), sim_mode=self.simulate)

    def patch_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        mapping: dict[str, str] = {}
        if patch.get("volume_level") is not None:
            mapping["volume"] = str(patch["volume_level"])
        if patch.get("language") is not None:
            mapping["language"] = str(patch["language"])
        if patch.get("memory_enabled") is not None:
            mapping["memory_enabled"] = "true" if patch["memory_enabled"] else "false"
        if patch.get("content_rating_max") is not None:
            mapping["content_rating_max"] = str(patch["content_rating_max"])
        if mapping:
            settings_repo.set_many(self.db, mapping, now=self.clock.now_iso())
        if patch.get("volume_level") is not None:
            self.player.set_level(str(patch["volume_level"]))
        return self.get_settings()

    def features(self) -> dict[str, Any]:
        """功能开关 + 当前 AI 后端标识。

        ``provider`` 供前端在「桩 / 真实云端」间如实展示（SPEC §2.3：未设 Key 必须落桩）；
        它是唯一非布尔字段，已同步写进 ``docs/api/openapi.yaml`` 的 ``/features``。
        """
        values = settings_repo.get_all(self.db)
        return {
            "memory": dto.as_bool(values.get("memory_enabled"), default=True),
            "child_mode": dto.as_bool(values.get("child_mode_enabled"), default=True),
            "night_mode": dto.as_bool(values.get("night_mode_enabled")),
            "mute": dto.as_bool(values.get("mute")),
            "sim_mode": bool(self.simulate),
            "provider": "stub" if self._ai_is_stub() else str(self.settings.ai.provider),
        }

    def _ai_is_stub(self) -> bool:
        return bool(getattr(self.settings.ai, "is_stub", True))
