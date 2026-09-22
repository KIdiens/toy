"""Qwen 真实提供方契约（**零网络**）：HTTPS 强制、无重定向、状态码分类、密钥不外泄。

用假 session 注入，既验证协议形状，又不产生任何真实请求。
"""
from __future__ import annotations

import pytest
from host.ai import qwen_provider
from host.ai.qwen_provider import QwenLlm, QwenTts, _HttpClient
from host.core.errors import ProviderError


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, chunks=()) -> None:
        self.status_code = status_code
        self._payload = payload
        self._chunks = chunks

    def json(self):
        return self._payload

    def iter_content(self, _size: int):
        yield from self._chunks


class FakeSession:
    """记录调用参数；``post``/``get`` 返回预置响应（不发任何网络请求）。"""

    def __init__(self, *, post_response=None, get_response=None) -> None:
        self.post_response = post_response or FakeResponse(200, {})
        self.get_response = get_response or FakeResponse(200, None, ())
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs))
        return self.post_response

    def get(self, url: str, **kwargs):
        self.gets.append((url, kwargs))
        return self.get_response


def _client(session: FakeSession, key: str = "secret-key") -> _HttpClient:
    return _HttpClient(key, session=session)


def test_missing_api_key_raises() -> None:
    with pytest.raises(ProviderError, match="DASHSCOPE_API_KEY"):
        _HttpClient("")


def test_plain_http_is_rejected_before_any_request() -> None:
    session = FakeSession()
    with pytest.raises(ProviderError, match="HTTPS"):
        _client(session).post_json("http://dashscope.aliyuncs.com/x", {})
    assert not session.posts, "非 HTTPS 必须在发出请求前就被拦下"


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_status_codes_are_classified(status: int) -> None:
    session = FakeSession(post_response=FakeResponse(status))
    with pytest.raises(ProviderError, match=str(status)):
        _client(session).post_json("https://dashscope.aliyuncs.com/x", {})


def test_unknown_status_has_generic_hint() -> None:
    session = FakeSession(post_response=FakeResponse(500))
    with pytest.raises(ProviderError, match="500"):
        _client(session).post_json("https://dashscope.aliyuncs.com/x", {})


def test_request_shape_bearer_and_no_redirects() -> None:
    session = FakeSession(post_response=FakeResponse(200, {"ok": 1}))
    client = _client(session, key="secret-key")
    assert client.post_json("https://dashscope.aliyuncs.com/x", {"a": 1}) == {"ok": 1}

    url, kwargs = session.posts[0]
    assert url.startswith("https://")
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["json"] == {"a": 1}


def test_error_message_never_leaks_the_key() -> None:
    session = FakeSession(post_response=FakeResponse(401))
    with pytest.raises(ProviderError) as info:
        _client(session, key="super-secret-token").post_json("https://x/y", {})
    assert "super-secret-token" not in str(info.value)


def test_non_object_json_is_rejected() -> None:
    session = FakeSession(post_response=FakeResponse(200, [1, 2, 3]))
    with pytest.raises(ProviderError, match="非对象"):
        _client(session).post_json("https://dashscope.aliyuncs.com/x", {})


def test_audio_url_must_be_https() -> None:
    session = FakeSession()
    with pytest.raises(ProviderError, match="HTTPS"):
        _client(session).get_bytes("http://cdn.example.com/a.wav")


def test_tts_output_must_be_wav() -> None:
    session = FakeSession(
        post_response=FakeResponse(200, {"output": {"audio": {"url": "https://cdn/a.mp3"}}}),
        get_response=FakeResponse(200, None, (b"ID3\x03",)),
    )
    with pytest.raises(ProviderError, match="不是 WAV"):
        QwenTts(_client(session)).synthesize("你好")


def test_tts_returns_audio_clip_for_wav_payload() -> None:
    wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 8
    session = FakeSession(
        post_response=FakeResponse(200, {"output": {"audio": {"url": "https://cdn/a.wav"}}}),
        get_response=FakeResponse(200, None, (wav,)),
    )
    clip = QwenTts(_client(session)).synthesize("你好")
    assert clip.is_wav and clip.data == wav


def test_llm_parses_tool_calls_from_json_arguments() -> None:
    payload = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {
            "name": "get_weather", "arguments": '{"city": "北京"}'}}],
    }}]}
    session = FakeSession(post_response=FakeResponse(200, payload))
    reply = QwenLlm(_client(session)).complete([])
    assert reply.tool_calls[0].name == "get_weather"
    assert reply.tool_calls[0].arguments == {"city": "北京"}


def test_llm_rejects_malformed_tool_arguments() -> None:
    payload = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"function": {"name": "x", "arguments": "{not json"}}],
    }}]}
    session = FakeSession(post_response=FakeResponse(200, payload))
    with pytest.raises(ProviderError, match="JSON"):
        QwenLlm(_client(session)).complete([])


def test_https_helper_rejects_non_https() -> None:
    with pytest.raises(ProviderError):
        qwen_provider._require_https("ftp://example.com/a")
