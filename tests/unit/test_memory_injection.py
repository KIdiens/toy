"""记忆删除后不再注入（AC-13）与注入前数据化封装（D8）。

用真实仓储 + 真实 SQLite，验证：确认记忆会进上下文；删除 / 未确认 / 过期记忆绝不进上下文。
"""
from __future__ import annotations

from host.adapters.services import StorageFacade
from host.ai.router import AiRouter
from host.ai.stub_provider import build_stub_triad
from host.ai.tools import ToolRegistry
from host.core.clock import VirtualClock
from host.storage import memories_repo


class _Facade(StorageFacade):
    """最小宿主：只提供 ``memory_context`` 需要的 ``db`` / ``clock``。"""

    def __init__(self, db, clock) -> None:
        self.db = db
        self.clock = clock
        self.simulate = True
        self.player = None


class _CapturingLlm:
    def __init__(self) -> None:
        self.messages = []
        self.calls = 0

    def complete(self, messages, tools=None, max_tokens=220):
        from host.ai.providers import LlmReply

        self.calls += 1
        self.messages = list(messages)
        return LlmReply(text="好的")


def _facade(temp_db) -> _Facade:
    return _Facade(temp_db, VirtualClock())


def test_confirmed_memory_is_injected_then_removed(temp_db) -> None:
    facade = _facade(temp_db)
    profile_id = facade.ensure_profile()
    facade.add_memory(profile_id=profile_id, kind="fact", value="喜欢恐龙")

    assert facade.memory_context(profile_id) == ["fact: 喜欢恐龙"]

    memory_id = facade.list_memories()[0]["id"]
    assert facade.delete_memory(memory_id)["deleted"] is True

    assert facade.memory_context(profile_id) == [], "删除后不得再进入注入上下文"


def test_deleted_memory_never_reaches_the_model(temp_db) -> None:
    facade = _facade(temp_db)
    profile_id = facade.ensure_profile()
    facade.add_memory(profile_id=profile_id, kind="fact", value="喜欢恐龙")
    facade.delete_memory(facade.list_memories()[0]["id"])

    llm = _CapturingLlm()
    asr, _stub_llm, tts = build_stub_triad()
    router = AiRouter(asr, llm, tts, ToolRegistry(),
                      memory_provider=lambda: facade.memory_context(profile_id))
    router.respond_text("我们聊点什么")

    joined = " ".join(m.content for m in llm.messages)
    assert "恐龙" not in joined, "已删除的记忆绝不能出现在模型上下文里"


def test_unconfirmed_memory_is_never_injected(temp_db) -> None:
    facade = _facade(temp_db)
    profile_id = facade.ensure_profile()
    facade.add_memory(profile_id=profile_id, kind="fact", value="喜欢恐龙", confirmed=False)

    assert facade.list_memories(), "列表里能看到（供人工确认）"
    assert facade.memory_context(profile_id) == [], "未确认记忆不参与注入"


def test_expired_memory_is_not_injected(temp_db) -> None:
    clock = VirtualClock()
    facade = _Facade(temp_db, clock)
    profile_id = facade.ensure_profile()
    memories_repo.add(temp_db, profile_id, "fact", "过期的事", now=clock.now_iso(),
                      confirmed=True, expires_at="2000-01-01T00:00:00Z")

    assert facade.memory_context(profile_id) == []


def test_default_profile_alias_resolves(temp_db) -> None:
    """前端用 ``profile_id="default"`` 指代默认档案，必须能解析而不是报非法编号。"""
    facade = _facade(temp_db)
    pid = facade.ensure_profile()
    assert facade._resolve_profile("default") == pid
    assert facade._resolve_profile(None) == pid
    assert facade._resolve_profile(str(pid)) == pid


def test_unknown_profile_string_needs_clarification(temp_db) -> None:
    import pytest
    from host.core.errors import ClarifyError

    facade = _facade(temp_db)
    facade.ensure_profile()
    with pytest.raises(ClarifyError, match="非法档案编号"):
        facade._resolve_profile("abc")
