"""零网络出口（AC-17）：无 ``DASHSCOPE_API_KEY`` 必须落桩，且对话过程零网络请求。

两条独立证据：
1. **静态**：桩模块源码不得 import 任何网络库（从源头排除出口）；
2. **动态**：把 ``socket`` 打成「一用就炸」，跑完一轮对话仍无异常 → 全程没有网络调用。
"""
from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest
from host.ai.router import AiRouter
from host.ai.stub_provider import StubAsr, StubLlm, StubTts, build_stub_triad
from host.ai.tools import ToolRegistry
from host.config.settings import load_settings

NETWORK_MODULES = frozenset({
    "socket", "ssl", "http", "urllib", "urllib3", "httpx", "requests", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "websockets", "grpc",
})
STUB_MODULES = (
    Path(__file__).resolve().parents[2] / "host" / "ai" / "stub_provider.py",
    Path(__file__).resolve().parents[2] / "host" / "ai" / "safety.py",
    Path(__file__).resolve().parents[2] / "host" / "ai" / "router.py",
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", STUB_MODULES, ids=lambda p: p.name)
def test_stub_stack_imports_no_network_module(path: Path) -> None:
    offenders = _imported_roots(path) & NETWORK_MODULES
    assert not offenders, f"{path.name} 不应 import 网络库: {sorted(offenders)}"


def test_no_api_key_falls_back_to_stub() -> None:
    settings = load_settings({})  # 显式空环境：既无 Key 也无 AI_PROVIDER
    assert settings.ai.api_key is None
    assert settings.ai.is_stub, "无 Key 时必须自动落桩（AC-17）"


def test_stub_reply_never_touches_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*_args, **_kwargs):
        raise AssertionError("桩后端不应发起任何网络连接")

    monkeypatch.setattr(socket, "socket", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)

    asr, llm, tts = build_stub_triad()
    router = AiRouter(asr, llm, tts, ToolRegistry())
    result = router.respond_text("你好呀")

    assert result.reply_text
    assert result.clip is not None and result.clip.is_wav


def test_build_ai_falls_back_when_qwen_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``AI_PROVIDER=qwen`` 但构造失败（缺 Key / 依赖）→ 回落桩而不是崩溃。"""
    from host.adapters import runtime
    from host.ai import qwen_provider

    def _boom(**_kwargs):
        raise qwen_provider.ProviderError("缺少 DASHSCOPE_API_KEY")

    monkeypatch.setattr(qwen_provider, "build_from_env", _boom)
    settings = load_settings({"AI_PROVIDER": "qwen"})
    assert not settings.ai.is_stub

    router = runtime._build_ai(settings, ToolRegistry(), None, _NullLogger())
    assert isinstance(router.asr, StubAsr)
    assert isinstance(router.llm, StubLlm)
    assert isinstance(router.tts, StubTts)


class _NullLogger:
    def info(self, *args, **kwargs) -> None: ...
    def warning(self, *args, **kwargs) -> None: ...
    def error(self, *args, **kwargs) -> None: ...
    def debug(self, *args, **kwargs) -> None: ...
