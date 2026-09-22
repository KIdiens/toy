# ADR-003: 使用领域线程 + 队列 + 单线程确定性核心作为并发模型

## Status
Accepted (2026-09-21)

## Background
硬需求（开发书 §7 明文）：「录音、网络、播放、串口分别使用可取消任务；**网络请求不能阻塞 MCU 通信和按钮响应**」。既有代码是阻塞同步风格（`pyserial` 阻塞读、`sounddevice`、`requests`）。同时要求「确定性测试」——无硬件阶段必须能不靠真实时间与真实网络复现时序。

## Decision
**领域线程 + 线程安全队列 + 单线程确定性核心（actor）**：

- `host/core/engine.py` 是唯一的状态持有者，**单线程**从 `queue.get()` 取事件，驱动状态机、下发命令。核心线程不阻塞在任何 I/O 上。
- 每个阻塞域一个专职线程：`serial`（串口读/写）、`audio`（采集/播放，半双工互斥）、`net`（云端/天气，带超时与有限重试）、`sched`（提醒/待机小动作）。
- 所有跨线程通信只经 `host/core/bus.py` 的线程安全队列；线程与队列数量固定，不动态起线程。
- FastAPI/uvicorn 的 asyncio 循环只服务 HTTP/WebSocket，经队列 + `run_coroutine_threadsafe` 与核心通信。
- 时间来源统一为注入的 `Clock`（`RealClock`/`VirtualClock`），核心与 `MotionClient` 都只依赖注入的 clock。

排除：asyncio 单循环 + `to_thread`（阻塞调用仍要下沉线程，且 `pyserial-asyncio` 自 2021 发布 0.6、2023 末休眠，仅覆盖 POSIX；不减少线程数反而增加时序调试面）；多进程（跨进程共享状态/锁复杂，时序不可控）。

## Consequences
- 正面：物理隔离——网络线程卡死不影响串口线程与按键响应；既有阻塞代码可原样复用；核心单线程 + 虚拟时钟 → 测试确定性、零真实等待。
- 负面：需谨慎处理线程生命周期与关停（统一 `EventKind.SHUTDOWN` + `join(timeout)`）；跨线程只传不可变数据类，禁止共享可变对象。
- 可测判据：注入 10s 假网络请求，断言串口与按键在 <100ms 内仍响应（`test_network_does_not_block_serial`）。

## Related ADRs
ADR-002（状态机在核心内单线程运行）、ADR-004（串口线程用 MotionLink）、ADR-011（虚拟时钟驱动模拟器）。
