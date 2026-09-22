# ADR-011: 用 MCU 模拟器 + 三来源串口 + 虚拟时钟实现无硬件可验证

## Status
Accepted (2026-09-21)

## Background
物料全部未采购：ESP32、舵机、独立电源、USB 音频皆无。但要求「全部功能在无硬件 + 无云端条件下可运行、可确定性测试」。既有 `software/test_protocol_simulation.py::FakeSerial` 只做 happy-path 假回答，无法表达时序故障（看门狗/稳定期/拔线/换角色）。开发书反复强调「每次只增加一个新的故障来源，并为它保留可复现的测试」（§22）。

## Decision
建立独立于 `host` 的 **MCU 模拟器** `sim/`，逐项复刻 `motion_controller.ino` 语义，并让**故障路径成为一等公民**：

1. `sim/controller.py` 与固件**同名同义**的状态机（`armed/haveSeq/previousSeq/activeRole/stableRole/candidateRole/currentUs/targetUs/lastFrame/...`），复刻 300ms 看门狗、200ms 角色稳定期、20ms/±6μs PWM 步进、64B/循环串口预算、行 99 字符上限、`sscanf` 解析细节（含 `ARM` 尾随 token、`previousSeq` 不重置等陷阱）。
2. **三来源互换**：`MotionLink` Protocol + `SerialLink`/`SimLink`/`ReplayLink`；模拟器以 TCP 服务端暴露，生产 `SerialLink` 用 pyserial 原生 `socket://` 连接——同一份串口代码对接真机与模拟器。
3. **虚拟时钟**：核心与模拟器都依赖注入 `Clock`；测试用 `VirtualClock` 手动推进时间，实现零真实等待的确定性测试。
4. **故障注入矩阵**：联锁拒绝、ALREADY_ARMED、帧号回退/越界/未解锁、看门狗超时、拔线、急停、运动中途换角色、行溢出、未知命令、PWM 初始化失败，每个场景一个 golden 行序列断言。
5. **录制回放**：JSONL 会话录制 + `--diff` 黄金比对，用于回归。

排除：仅放大 `FakeSerial`（无时序语义）；`loop://`（只回环）。

## Consequences
- 正面：真板之前即可把全部协议与故障路径测完；测试确定可复现；模拟器与固件对照可审查。
- 负面：模拟器是**第二份实现**，存在与固件漂移的风险 → 缓解：函数同名同义 + golden 行序列 + 「任何模拟器独有回包都视为缺陷」的审查纪律。
- 边界（诚实声明）：不能证明舵机真实运动/中位/电气急停/PWM 时延/ADC 窗口/温升（见架构 §10.1）。理想分压与理想时基需实测后回写。

## Related ADRs
ADR-002（状态机确定性）、ADR-003（虚拟时钟）、ADR-004（串口抽象）、ADR-008（Null 音频，共同构成无硬件档位）。
