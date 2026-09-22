# ADR-010: 云端 AI 使用 Provider Protocol + Stub/Qwen 可切换实现

## Status
Accepted (2026-09-21)

## Background
本阶段**不接阿里云百炼**，但 §10 的完整链路（ASR→LLM→TTS）、§12.2 的工具调用、§10.5 的错误分类（401/403/404/429）与降级都需要被显式设计并可测。要求：Key 到位后**改一处配置即切换**，接口形状与真实服务完全一致。

## Decision
定义三个 Protocol 与统一数据类：

- `AsrProvider.transcribe(wav: bytes, lang: str|None) -> str`
- `LlmProvider.complete(messages, tools: list[ToolSpec]|None, max_tokens) -> LlmReply`（`LlmReply` 含文本与 `tool_calls`）
- `TtsProvider.synthesize(text, voice, lang) -> AudioClip`

实现：
- `Stub*`（本阶段默认）：从 fixture 给出**确定性**结果——ASR 返回脚本化文本、LLM 返回脚本化文本/`tool_calls`、TTS 返回固定 WAV，供端到端与回归使用。
- `Qwen*`：复用 `software/cloud_chat.py` 已验证的 REST 形状（HTTPS-only、`Authorization: Bearer`、非流式、`qwen3-asr-flash`/`qwen-plus`/`qwen3-tts-flash`、401/403/404/429 分类、TTS 音频 URL 必须 HTTPS、WAV 头部校验）。

切换：`AI_PROVIDER=stub|qwen`（配置），无需改代码。同一套**契约测试**跑两边：对 `Stub` 必跑，对 `Qwen` 无 `DASHSCOPE_API_KEY` 时 `skip`。

排除：直接调用 `cloud_chat.py` 的函数（无接口形状约束，测试无法替换）；引入官方 `dashscope` SDK（版本耦合，与「减少 SDK 耦合」的示例取向相悖）。

## Consequences
- 正面：本阶段无 Key 可端到端；切换只改配置；错误分类与降级在桩上即可测；工具调用协议（§12.2）在 `LlmReply.tool_calls` 上统一。
- 负面：需维护两套实现并保持契约一致（靠契约测试守护）。
- 安全：密钥只从环境/仅服务账户可读文件读取，不进日志、不回显（§10.2/§13.2）；输入输出审核在出声前完成（§15）。

## Related ADRs
ADR-003（网络线程）、ADR-006（设置接口回显密钥约束）。
