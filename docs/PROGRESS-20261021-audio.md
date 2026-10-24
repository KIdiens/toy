# 音频后端接入进展

日期：2026-10-21。基于交接版本 `f19d35a` 的工作区修改，尚未创建提交或推送。

## 这轮改了什么

此前启动装配固定使用 `NullAudioBackend()`。虽然仓库已有真实声卡后端，用户无法通过启动参数启用它；真实录音返回的 16-bit PCM 也被直接送入要求 WAV 的 ASR 接口。

现在已完成以下软件接入：

- 配置加载增加 `[audio]`，默认 `null`；可显式选择 `sd`，分别指定输入设备、输出设备与录音采样率。
- CLI 参数覆盖环境变量，再回落到 TOML。命令行解析与中文配置提示放入 `host/config/cli.py`；`host.main` 保留原有导入符号，入口继续负责装配与启动。
- 启动装配按所选配置创建后端，录音器和播放器共享同一个实例。真实模式先检查依赖、输入、输出和配置采样率；检查失败会报中文错误并退出，不会改用桩音频。
- 设备检查或后续装配失败时，清理音频后端与数据库资源；正常启动后交给 `AppContext.close()` 管理。
- ASR 入口为原始 PCM 添加 WAV 容器，保留采样率、声道数和采样内容；已有 RIFF 夹具继续透传。录音后端及 VAD 的 PCM 契约保持原样。
- README 补充设备枚举、启动用法与验收边界，并按实测修正测试计数和跳过原因。

## 怎么使用

在 `toy/` 目录的 PowerShell 中执行：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& '.\.venv\Scripts\python.exe' -m sounddevice
& '.\.venv\Scripts\python.exe' -m host.main --simulate --audio sd
```

`--simulate` 控制 MCU，`--audio` 控制声卡，两项互相独立。输入、输出不是同一个设备时，可增加 `--audio-input-device` 和 `--audio-output-device`，值为枚举结果中的编号或名称。`--audio-sample-rate` 配置录音采样率，默认 16000 Hz。

环境变量为 `AUDIO_BACKEND`、`AUDIO_INPUT_DEVICE`、`AUDIO_OUTPUT_DEVICE`、`AUDIO_SAMPLE_RATE`。这些参数不切换 AI 提供方；默认桩后端仍不做真实语音识别。

## 验证与证据

本轮新增 13 项回归，覆盖 CLI 优先级、设备选择、无依赖时的默认启动、真实后端共享、PCM 到 ASR 的格式、TTS 到所选输出、设备故障退出及装配失败清理。

| 验证 | 结果 | 证据位置（相对 `toy/`） |
|---|---|---|
| 全量回归 | 1575 passed / 0 failed / 3 skipped；共 1578 项 | `data/audio-integration-20261021/pytest-final.xml`、`pytest-final.log` |
| 启动相关回归 | 32 项通过 | `data/audio-integration-20261021/startup-final.xml` |
| Ruff | 全仓通过 | `data/audio-integration-20261021/ruff.txt` |
| 实际进程启动 | `null`、`sd` 均能提供状态 HTTP 接口 | `data/audio-integration-20261021/startup-smoke.json`、`startup-null.log`、`startup-sd.log` |
| 不存在的设备 | 未启动 Web，退出码 3，中文指引包含设备配置项 | `data/audio-integration-20261021/startup-invalid-device.log` |
| 测试判别力 | 强制选回空后端时，实例断言失败；跳过 WAV 封装时，独立 WAV 解析器失败 | `data/audio-integration-20261021/mutation-results.json`、`mutation-*.log` |

变异检查在进程内替换实现，不改写源码；两种变异在独立进程中验证，正常实现随后复验为绿。3 条跳过仍是角色限幅样本无法构造，与音频和真机权限无关。

## 尚待推进

- 录音提前结束与播放打断：现有 audio 队列由单线程消费，真实后端的 `record()` / `play()` 会阻塞。需专门复现并处理停止命令排队及取消竞态，本轮没有宣称这些时序已经验收。
- 真实麦克风录音、扬声器出声、音质、设备热插拔、长时间运行和动作同步。本轮只做设备格式检查与服务启动，没有采集环境声音或播放声音。
- 真实 ASR/LLM/TTS：本轮未使用云端 Key，设备替身链路中的模型仍为桩。C6、C9 及其它实物项继续待验证。
- 接手时已登记的 `pages/editor.js` 行数超限、OD-8 等事项，未在本轮混入处理。

本轮完成的是音频配置、装配和格式传递；整机验收仍按架构规格 §10.1 的 C1–C13 逐项取证。

后续已推进录音提前结束、播放打断和关停取消，详见 [取消时序进展](PROGRESS-20261021-audio-cancel.md)。本文件保留音频接入时的阶段记录。
