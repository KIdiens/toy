# ADR-008: 使用 sounddevice + soundfile 并叠加 Null 后端作为音频抽象

## Status
Accepted (2026-09-21)

## Background
系统需在 Windows 开发、树莓派 4B 部署，两边的音频栈完全不同（Windows WASAPI/MME vs Linux ALSA/PulseAudio）。开发书 §8.3 要求「完整应用统一使用同一个音频后端，而不是多个进程竞争声卡」；§7.1 要求半双工（说话时不连续监听，先停播再录音）；无硬件阶段还要能在**没有声卡**的机器上跑通全链路。

## Decision
定义 `AudioBackend` Protocol（`open/record/play/stop/close`），三个实现：

- `SoundDeviceBackend`（生产）：`sounddevice 0.5.6`（PortAudio 绑定，MIT，2026-08-17）负责采集/播放；`soundfile 0.14.0`（libsndfile 绑定，BSD-3，2026-06-06，wheel 自带 libsndfile）负责 WAV/FLAC/OGG/MP3 读写与格式统一。
- `NullAudioBackend`（无硬件/CI 默认）：以固定时长「虚拟录制/播放」，产生与真实后端**同形状**的结果（时长、采样率、完成事件），使全链路无设备可跑。
- （测试）`ScriptedAudioBackend`：以 fixture WAV 充当录制输入，供端到端用例。

全局只允许**一个** `AudioBackend` 实例；采集与播放互斥（半双工），由 `host/audio/player.py` 与 `recorder.py` 在同一后端上串行化。

排除：PyAudio 0.2.14（Windows 需预编 wheel、维护弱于 sounddevice）；`arecord`/`aplay` 子进程（Windows 无、两套代码，违反「统一后端」）。`ffmpeg` 仅用于离线格式转换（§8.2），不进运行时链路。

## Consequences
- 正面：单一 API 覆盖 Windows 与 Pi；wheel 自带原生库，免系统依赖；Null 后端让无硬件全链路可测。
- 负面：PortAudio 采样率/设备选择在两端有差异（需在配置中按设备名指定，避免 `plughw`/重采样变速，§8.3）；`sounddevice` 要求 Python ≥3.7（满足）。
- 目标与现状：动作↔音效同步 <100ms（§9.4）是**目标**，需声卡到位后实测，本阶段只保证时序逻辑（统一单调时钟采样）。

## Related ADRs
ADR-001（运行时）、ADR-003（音频线程）、ADR-011（无硬件 E2E）。
