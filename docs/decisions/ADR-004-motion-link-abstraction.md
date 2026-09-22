# ADR-004: 使用 MotionLink 协议与 Serial/Sim/Replay 三实现做串口抽象

## Status
Accepted (2026-09-21)

## Background
无硬件阶段必须在没有 ESP32 的条件下验证串口链路；真板到位后又必须不改业务代码即可切换到真实串口；回归时还要能回放录制会话。既有 `software/motion.py::play` 直接 `import serial` 并用 `.readline()/.in_waiting/.write/.reset_input_buffer`，`test_protocol_simulation.py` 靠 monkeypatch `sys.modules['serial']` 替换——该手法脆弱且无法表达时序故障。

## Decision
定义 `MotionLink` Protocol：`open/close/write_line/read_lines/reset_input_buffer`。三个实现：

- `SerialLink`（生产）：`pyserial 3.5`。**关键点**：用 `serial.serial_for_url(MOTION_PORT)`，使同一份代码既能连真实设备（`COM5`、`/dev/serial/by-id/...`），也能连模拟器的 `socket://127.0.0.1:5555`——**业务代码零 `if sim:` 分支**。
- `SimLink`（测试/开发）：进程内直连 `sim.McController` + `VirtualClock`，确定性、可注入故障。
- `ReplayLink`（回归）：JSONL 录制/回放与 `--diff` 黄金比对。

`host/motion/client.py` 保留 `software/motion.py::play` 的全部顺序与阈值（STATE 前置检查 → ARM 1s 确认 → 持续 FRAME → 末帧保持 ≥150ms → STOP；ERR/FAULT 即中止；状态回报超时 0.8s；收尾总超时 end+2s），仅把 monkeypatch 改为依赖注入。

排除：pyserial 的 `loop://`/仅 URL 方案（只回环，无法注入故障与状态机）；`pyserial-asyncio 0.6`（休眠、POSIX-only）。

## Consequences
- 正面：真实/模拟/回放三来源互换且不改调用方；生产代码不含测试分支；故障注入成为可能。
- 负面：`SerialLink` 需处理 `socket://` 重连（连接断开时 `open` 抛错由上层按「通信中断」处理，符合开发书 §18.4「断串口进入锁定」）。
- 兼容：`software/motion.py` 与现有 monkeypatch 测试**保持不变**（冻结基线）；新包另建 DI 测试。

## Related ADRs
ADR-003（串口线程）、ADR-011（模拟器 TCP 服务端）。
