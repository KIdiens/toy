"""Qwen（阿里云百炼）真实 REST 提供方，复用 ``software/cloud_chat.py`` 已验证形状。

复刻要点（架构 §5 兼容契约）：

- 云 API **强制 HTTPS**；``allow_redirects=False``；``Authorization: Bearer``；**非流式**。
- 错误**分类**：401/403/404/429 各有明确语义，统一抛 ``ProviderError``（不回显用户内容与密钥）。
- TTS 返回的音频 URL 必须 HTTPS；下载限额；必须是 WAV（``RIFF``/``WAVE``），否则报错。

本模块仅在 ``AI_PROVIDER=qwen`` 且设置了 ``DASHSCOPE_API_KEY`` 时使用；无 Key 时系统落桩，
本模块不会被实例化（AC-17）。密钥只从环境/只读文件读取，不落盘、不进日志。
"""
from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urlparse

import requests

from host.ai.providers import AudioClip, LlmReply, Message, ToolCall, ToolSpec
from host.core.errors import ProviderError

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TTS_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
DEFAULT_ASR_MODEL = "qwen3-asr-flash"
DEFAULT_CHAT_MODEL = "qwen-plus"
DEFAULT_TTS_MODEL = "qwen3-tts-flash"
TIMEOUT = (10, 60)
MAX_WAV_BYTES = 7_000_000
MAX_AUDIO_BYTES = 10_000_000

STATUS_HINTS: dict[int, str] = {
    401: "鉴权失败（401），请检查 DASHSCOPE_API_KEY",
    403: "无权访问（403），请检查地域与权限",
    404: "模型或路径不存在（404），请检查模型名",
    429: "触发限流（429），请降低频率或提升配额",
}


def _require_https(url: str) -> None:
    if urlparse(url).scheme != "https":
        raise ProviderError("云 API 必须使用 HTTPS")


def _classify(status: int) -> ProviderError:
    hint = STATUS_HINTS.get(status, f"云 API 返回状态码 {status}")
    return ProviderError(hint)


class _HttpClient:
    """极薄 HTTP 封装：密钥注入、HTTPS 强制、无重定向、状态码分类。"""

    def __init__(self, api_key: str, *, timeout: tuple[int, int] = TIMEOUT, session=None) -> None:
        if not api_key:
            raise ProviderError("缺少 DASHSCOPE_API_KEY")
        self._api_key = api_key
        self._timeout = timeout
        self._session = session or requests.Session()

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_https(url)
        response = self._session.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise _classify(response.status_code)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError("云 API 返回了非对象 JSON")
        return payload

    def get_bytes(self, url: str, *, limit: int = MAX_AUDIO_BYTES) -> bytes:
        _require_https(url)
        response = self._session.get(url, timeout=self._timeout, stream=True, allow_redirects=False)
        if response.status_code != 200:
            raise _classify(response.status_code)
        data = bytearray()
        for part in response.iter_content(65536):
            data.extend(part)
            if len(data) > limit:
                raise ProviderError(f"音频超过 {limit} 字节上限")
        return bytes(data)


class QwenAsr:
    """语音转文字（``qwen3-asr-flash``）。"""

    def __init__(self, client: _HttpClient, *, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_ASR_MODEL) -> None:
        self._client = client
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._model = model

    def transcribe(self, wav: bytes, lang: str | None = None) -> str:
        if not 0 < len(wav) < MAX_WAV_BYTES:
            raise ProviderError(f"WAV 大小非法（限 {MAX_WAV_BYTES} 字节以内）")
        encoded = base64.b64encode(wav).decode("ascii")
        body = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": "data:audio/wav;base64," + encoded},
                }],
            }],
            "stream": False,
            "asr_options": {"enable_itn": True},
        }
        payload = self._client.post_json(self._endpoint, body)
        text = _first_content(payload)
        if not text.strip():
            raise ProviderError("没有识别到有效文本")
        return text


class QwenLlm:
    """文本生成（``qwen-plus``），支持工具调用。"""

    def __init__(self, client: _HttpClient, *, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_CHAT_MODEL) -> None:
        self._client = client
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._model = model

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 220,
    ) -> LlmReply:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "max_tokens": max_tokens,
            "enable_thinking": False,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        message = _first_message(self._client.post_json(self._endpoint, body))
        text = message.get("content") or ""
        calls = tuple(_parse_tool_call(c) for c in message.get("tool_calls") or ())
        if not isinstance(text, str):
            raise ProviderError("模型返回的内容类型非法")
        return LlmReply(text=text, tool_calls=calls)


class QwenTts:
    """文字转语音（``qwen3-tts-flash``）。"""

    def __init__(self, client: _HttpClient, *, tts_url: str = DEFAULT_TTS_URL,
                 model: str = DEFAULT_TTS_MODEL) -> None:
        self._client = client
        self._url = tts_url
        self._model = model

    def synthesize(self, text: str, voice: str = "Cherry", lang: str | None = None) -> AudioClip:
        if not text.strip():
            raise ProviderError("没有可朗读文本")
        body = {"model": self._model,
                "input": {"text": text, "voice": voice, "language_type": lang or "Auto"}}
        payload = self._client.post_json(self._url, body)
        try:
            audio_url = payload["output"]["audio"]["url"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("TTS 响应缺少音频地址") from exc
        data = self._client.get_bytes(audio_url)
        if not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
            raise ProviderError("服务返回的音频不是 WAV，需转换后播放")
        return AudioClip(data=data, sample_rate=24000)


def _first_message(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("云 API 响应缺少 choices[0].message") from exc
    if not isinstance(message, dict):
        raise ProviderError("云 API 响应 message 类型非法")
    return message


def _first_content(payload: dict[str, Any]) -> str:
    content = _first_message(payload).get("content")
    if isinstance(content, list):  # 兼容 content 分片形状
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    import json  # noqa: PLC0415  （仅解析模型返回的 argument 字符串时需要）

    function = raw.get("function") or {}
    args = function.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError as exc:
            raise ProviderError("工具参数不是合法 JSON") from exc
    return ToolCall(
        name=str(function.get("name", "")),
        arguments=args if isinstance(args, dict) else {},
        call_id=str(raw.get("id", "")),
    )


def build_from_env(*, session=None) -> tuple[QwenAsr, QwenLlm, QwenTts]:
    """从环境变量构造三元组；缺 Key 时抛 ``ProviderError``（调用方应落桩）。"""
    client = _HttpClient(os.environ.get("DASHSCOPE_API_KEY", ""), session=session)
    base = os.getenv("QWEN_BASE_URL", DEFAULT_BASE_URL)
    return (
        QwenAsr(client, base_url=base, model=os.getenv("QWEN_ASR_MODEL", DEFAULT_ASR_MODEL)),
        QwenLlm(client, base_url=base, model=os.getenv("QWEN_CHAT_MODEL", DEFAULT_CHAT_MODEL)),
        QwenTts(client, tts_url=os.getenv("QWEN_TTS_URL", DEFAULT_TTS_URL),
                model=os.getenv("QWEN_TTS_MODEL", DEFAULT_TTS_MODEL)),
    )
