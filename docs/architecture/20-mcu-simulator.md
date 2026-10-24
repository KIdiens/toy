# MCU 模拟器设计（无硬件可验证方案）

> 状态：Accepted（2026-09-21）· 负责人：高见远（首席架构师）· 对应 ADR-011
> 目标：在**没有任何硬件**的电脑上，替代 ESP32，**逐字复用** `firmware/motion_controller/motion_controller.ino` 的协议语义与**故障路径**，让 `toy/host` 全链路可运行、可确定性测试。
> 原则：模拟器是**设备模型**，不是「happy path 假回答器」。它存在的意义是**在真板之前把故障注入跑完**。

## 1. 为什么不复用现有 `FakeSerial`

`software/test_protocol_simulation.py::FakeSerial` 只对少量命令返回预设字符串（ARM 成功/失败、永远 SETTLED、一个 FAULT 开关）。它：

- 无时间语义（不模拟 300ms 看门狗、200ms 稳定期、20ms PWM 步进）；
- 无角色稳定/联锁状态机（不区分 `candidateRole`/`stableRole`/`activeRole`）；
- 无法表达「运动中拔线」「换角色」「帧号回退」这类时序故障。

因此新模拟器**不是 FakeSerial 的放大版，而是固件的等价状态机**。现有 `FakeSerial` 与其测试**保持不变**（冻结基线）。

## 2. 模拟器结构

```
sim/
├── __main__.py      CLI 入口：--tcp <port> | --pipe | --scenario <json> | --replay <jsonl> [--diff]
├── controller.py    固件等价状态机（含 PWM 步进、看门狗、联锁、报告节奏）
├── wire.py          字节流协议层（行帧、99 字符上限、每循环 64B、\r 跳过、溢出）
├── devices.py       Dock/Estop/RoleResistor/Buttons（含去抖与注入接口）
├── scenario.py      虚拟时间线脚本（在虚拟 ms 上调度：拔线/急停/换角色/断流/按键）
└── tcp_bridge.py    TCP 服务端，接受 pyserial `socket://` 连接
```

## 3. 与固件的逐项语义对照（保真矩阵）

`sim/controller.py` 的函数与状态变量**故意与 `.ino` 同名同义**，以便逐行对照审查。

| 固件元素 | 固件行为 | 模拟器实现 | 保真要点 |
|----------|----------|-----------|----------|
| `setup()` | `READY 1`；`ledcAttach` 失败则 `FAULT PWM_INIT` | `McController(pwm_ready=True/False)`，启动时发 `READY 1` 或 `FAULT PWM_INIT` | 允许注入 `pwm_ready=False` 走故障分支 |
| `stopMotion()` | `armed=false; haveSeq=false; PWM=0`；**不重置 `previousSeq`** | 同 | 复刻「`previousSeq` 不重置」这一潜在陷阱（见架构 §10.2 D3） |
| `report()` | 发 `STATE %d %d %d %d`（armed,docked,healthy,stableRole）；当 `settled&&haveSeq` 发 `SETTLED <previousSeq>` | 同 | `settled` 判据 = `armed && haveSeq && currentUs[i]==targetUs[i] ∀i` |
| `command("STOP")` | stopMotion；`OK STOP` | 同 | |
| `command("STATUS")` | `report()` | 同 | STATUS **不刷新**看门狗 |
| `ARM <role>` 解析 | `sscanf("ARM %d %c")==1` | 复刻该解析（尾随多余 token → 落 FRAME 分支失败 → `ERR COMMAND`） | 回归点（架构 §10.2 D6） |
| `ARM` 联锁 | `!pwmReady \|\| !docked \|\| !healthy \|\| !stableRole \|\| readRole()!=stableRole \|\| expectedRole!=stableRole \|\| candidateRole!=stableRole \|\| millis()-candidateSince<200` → `ERR INTERLOCK` | 同，且**按同一次序**求值 | 次序影响错误归因（先报哪个联锁） |
| `ARM` 重复 | `armed` → `ERR ALREADY_ARMED` | 同 | |
| `ARM` 成功 | `activeRole=stableRole; current=target=1500; haveSeq=false; lastFrame=now; armed=true; OK ARM <role>` | 同 | |
| `FRAME` 解析 | `sscanf("FRAME %lu %d %d %d %d %c")==5` | 同 | `%lu` 语义：模拟器内 seq 用 32 位无符号 |
| `FRAME` 有效性 | `valid = armed && (!haveSeq \|\| seq>previousSeq)` 且四值 ∈[1400,1600]；无效 → `stopMotion(); ERR FRAME` | 同 | 无效帧会**停机** |
| `FRAME` 有效 | `targetUs=values; previousSeq=seq; haveSeq=true; lastFrame=now`；**无 ACK** | 同 | 回包靠 STATE/SETTLED 推断 |
| 未知命令 | `stopMotion(); ERR COMMAND` | 同 | |
| 行溢出 | `used>=99` 置 `overflowLine`；遇 `\n` → `stopMotion(); ERR LENGTH` | 同 | `wire.py` 负责缓冲与溢出 |
| 主循环角色稳定 | `role=docked()?readRole():0`；变化则 `candidateSince=now;stableRole=0`；未到位→`stableRole=0`；`now-candidateSince>=200`→`stableRole=candidateRole` | 同 | 200ms 稳定期是联锁关键 |
| 主循环联锁 | `armed && (!docked \|\| !healthy \|\| role!=activeRole \|\| now-lastFrame>300)` → `stopMotion(); FAULT INTERLOCK_OR_TIMEOUT` | 同 | **看门狗 300ms** |
| 串口预算 | 每循环最多处理 64 字节 | `wire.feed()` 每 tick 限量 64B | 通信洪泛不阻塞安全检查 |
| PWM 步进 | 每 20ms：`currentUs[i]+=constrain(target-current,-6,6)`；duty=`(us*16383+10000)/20000` | 同（并记录 duty 供断言） | 限速 ≈300μs/s |
| 按钮 | 去抖 30ms；下降沿发 `BUTTON <n>` | 同（`devices.Buttons`） | |
| 周期报告 | 每 200ms 一次 `report()` | 同 | |
| `readRole()` | `analogReadMilliVolts` → 三个窗口 150–450/850–1250/1450–1900 → 1/2/3，否则 0 | `devices.RoleResistor` 由**公仔电阻**算理论 mV：`3300*R/(10000+R)`，再套同窗口 | 用理想分压；窗口**未实测**（架构 §10.1 C4） |

> 保真原则：**宿主解析到的每一行，都必须能由模拟器在真实固件里被产出**。任何模拟器独有的返回都是缺陷。

## 4. 三类串口来源互换（`MotionLink` Protocol）

```python
class MotionLink(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write_line(self, line: str) -> None: ...      # 追加 "\n"
    def read_lines(self) -> list[str]: ...            # 非阻塞取回已到达的整行
    def reset_input_buffer(self) -> None: ...
```

| 实现 | 使用场景 | 关键技术 |
|------|----------|----------|
| `SerialLink` | **真实 USB 串口** 与 **模拟器**（同一份代码） | `pyserial`：真实设备用 `COM5` / `/dev/serial/by-id/...`；模拟器用 **`serial.serial_for_url("socket://127.0.0.1:5555")`** |
| `SimLink` | 单元/集成测试（进程内、确定性） | 直接持有 `McController` + `VirtualClock`，`write_line` → `wire.feed` → `controller.tick` → 取回出站行 |
| `ReplayLink` | 回归（录制回放 / 黄金比对） | JSONL 会话文件（格式见 **§4.1**）：首行元信息 + 事件行（`host`/`inject`/`wait`）+ 期望行（`mcu`）；`--diff` 逐行比对 `(t_ms, line)` |

- **同一份串口代码对接真机与模拟器**：这是选 `socket://` 的关键收益——`host/motion/serial_link.py` 无需任何 `if sim:` 分支。生产连接字符串来自配置（`MOTION_PORT`），值可以是 `COM5`、`/dev/serial/by-id/usb-...` 或 `socket://127.0.0.1:5555`。
- `SimLink` 与 `ReplayLink` 仅存在于测试/开发路径，生产代码不 import。
- TCP 服务端的固件主循环必须独立于输入流量持续推进：没有 STATUS/FRAME 输入时，角色稳定采样、周期 STATE 与丢帧看门狗仍自行运行；真实 socket 回归见 `tests/integration/test_tcp_idle.py`。

### 4.1 录制会话格式（`sim/session.py`）

录制/回放由 `sim/session.py` 负责（`Session` + `save` / `load` / `replay`）。会话是 JSONL：**首行为元信息**，其后为事件行与期望行。

| 行 | 形状 | 语义 |
|----|------|------|
| 元信息（首行） | `{"session":"toy-session-v1","controller_args":{...},"end_ms":N}` | 会话标识、**控制器初态构造参数**（`pwm_ready` / `role` / `docked` / `estop_ok`）、场景末步时刻 |
| 事件 `host` | `{"t_ms":N,"dir":"host","line":"ARM 1"}` | 主机向 MCU 写一行（等价串口写） |
| 事件 `inject` | `{"t_ms":N,"dir":"inject","action":"dock","args":{"engaged":false}}` | **设备初态 / 中途物理态注入**（拔线 / 急停 / 换角色 / 按键） |
| 事件 `wait` | `{"t_ms":N,"dir":"wait"}` | 只推进虚拟时间，不产生任何输入 |
| 期望 `mcu` | `{"t_ms":N,"dir":"mcu","line":"OK ARM 1"}` | 期望 MCU 产出的行（含时间戳） |

- **为何录制必须携带设备初态**：非默认物理态的场景（拔线 `interlock_dock_open`、急停 `estop_during_motion`、`pwm_init_fail`）**其行为由设备初态决定**。若录制只存主机发出的行，回放时设备从默认态起步，这些故障**根本无法被复现**——录制→回放→逐行比对的前提就会塌掉。因此用 `controller_args`（构造参数）+ `dir=inject`（t=0 的初态注入与中途态变）共同重建物理态。
- **回放推进到 `end_ms`**（而非最后一条 `host` 行）：末条主机行之后仍有**时间驱动**的输出（看门狗 `FAULT INTERLOCK_OR_TIMEOUT`、200ms 周期 `STATE`），必须推进到场景末步才能录全、也比对得全。
- **比对口径**：`(t_ms, line)` **逐行**一致，不只对末帧。
- **同节奏驱动**：回放对每个事件做「`advance_to` → `settle` → 作用 → `poll` → `settle`」，与 `Scenario.run` 完全一致。`sim/scenario.py` 导出 `settle`（按每圈 64 字节预算**排空 RX** 直到无待处理字节）与 `advance_to`（虚拟时钟**只前进不后退**）——这是「录制 = 回放」能逐行成立的前提。
- **向前兼容**：`load()` 能识别无 `session` 头的旧格式（仅 `host`/`mcu` 两种行）继续读取。

## 5. 控制时钟：真实与虚拟

```python
class Clock(Protocol):
    def now_ms(self) -> int: ...
    def sleep(self, seconds: float) -> None: ...

class RealClock:   # time.monotonic() * 1000
class VirtualClock: # 测试: advance(ms) 手动推进; 不真实睡眠
```

- 模拟器与 `MotionClient` **都只依赖注入的 `Clock`**。生产用 `RealClock`；测试用 `VirtualClock`，实现**零真实等待的确定性**。
- 例：验证看门狗 —— `clock.advance(301)` 后调 `controller.tick()`，断言得到 `FAULT INTERLOCK_OR_TIMEOUT`；验证 200ms 稳定期 —— `arm()` 后立即 `advance(<200)` 断言 `ERR INTERLOCK`，再 `advance` 越过 200ms 断言 `OK ARM 1`。

## 6. 故障注入（一等公民）

`scenario.py` 提供在虚拟时间上调度的事件，覆盖固件所有会走向非 happy path 的分支：

| 场景 id | 注入 | 期望固件输出 |
|---------|------|--------------|
| `interlock_dock_open` | 未到位 | `ARM` → `ERR INTERLOCK` |
| `interlock_estop` | 急停按下（`healthy=false`） | `ARM` → `ERR INTERLOCK` |
| `interlock_role_mismatch` | 公仔角色 ≠ `ARM` 参数 | `ERR INTERLOCK` |
| `interlock_unstable` | 到位但 <200ms | 首次 `ARM` → `ERR INTERLOCK`；稳定后 → `OK ARM 1` |
| `already_armed` | 二次 `ARM` | `ERR ALREADY_ARMED` |
| `frame_out_of_range` | `FRAME 0 1500 1500 1500 1700` | `ERR FRAME` + PWM 停 |
| `frame_seq_regress` | `FRAME 5` 后 `FRAME 4` | `ERR FRAME` + PWM 停 |
| `frame_unarmed` | 未 `ARM` 直接发帧 | `ERR FRAME` + PWM 停 |
| `watchdog_timeout` | 运动中停发帧 >300ms | `FAULT INTERLOCK_OR_TIMEOUT` |
| `unplug_during_motion` | 运动中到位开关释放 | `FAULT` |
| `estop_during_motion` | 运动中急停 | `FAULT` |
| `swap_role_during_motion` | 运动中换公仔角色 | `FAULT`（`role!=activeRole`） |
| `line_overflow` | 发送 >99 字符一行 | `ERR LENGTH` + PWM 停 |
| `unknown_command` | `FOO` | `ERR COMMAND` + PWM 停 |
| `pwm_init_fail` | 构造 `pwm_ready=False` | 启动即 `FAULT PWM_INIT` |

- 每个场景一个 golden 断言文件（`tests/fixtures/sim/<id>.jsonl`），断言**出站行序列与顺序**，不只断言最后一帧。
- 「通信洪泛不阻塞安全」：注入持续满速 FRAME 洪泛 + 同时按急停，断言 `FAULT` 仍在下一次循环内产生（复刻 64B/循环预算）。

## 7. 三种运行形态

1. **进程内（测试/开发）**：`McController` + `VirtualClock` + `SimLink`，零网络、零真实时间。
2. **TCP 服务端（集成/E2E，对接真实主机进程）**：
   ```
   python -m sim --tcp 5555 --role 1 --docked --estop-ok
   # 另开: MOTION_PORT=socket://127.0.0.1:5555 python -m host.main --simulate
   ```
   `tcp_bridge.py` 每接受一个连接，把该连接接成一个 socket 客户端；pyserial `socket://` 打开的即是它。可反复断开重连（测试「通信中断恢复」）。
3. **脚本化场景（回归）**：`python -m sim --scenario tests/fixtures/sim/watchdog_timeout.json` 打印逐行时间轴，供人工审阅与黄金比对。

## 8. 主机侧一致性：`MotionClient`

`host/motion/client.py` 必须**保留** `software/motion.py::play` 的顺序与阈值（架构 §5）：STATE 前置检查 → ARM（1s 确认窗口）→ 持续 FRAME → 末帧保持 ≥150ms → STOP；`ERR/FAULT` 即中止；状态回报超时 0.8s；收尾总超时 `end_ms/1000 + 2s`。差异只有两点，均为改进且不改变对外行为：

1. 依赖注入 `Clock` + `MotionLink`（替代 monkeypatch）；
2. 动作**校验**改由 `host/motion/validation.py` 单一真源提供，并有 fuzz 一致性测试对齐旧 `motion.py`。

## 9. 端到端（无硬件）验证命令

```bash
# 1) 契约：动作校验与旧 motion.py 一致
pytest tests/contract -q

# 2) 模拟器全部故障路径
pytest tests/integration -q

# 3) 非阻塞证明
pytest tests/integration -q -k network_does_not_block_serial

# 4) 无硬件全链路
python -m host.main --simulate           # 模拟器 + Stub AI + NullAudio + 网页
curl -s localhost:8000/api/v1/status

# 5) 黄金回放比对
python -m sim --replay tests/fixtures/sessions/dialog_round.jsonl --diff
```

## 10. 已知边界（诚实声明）

- 模拟器验证的是**逻辑与协议**，**不能**证明：舵机真实运动、中位校准、电气急停断电、PWM 时延、ADC 窗口精度、温升（见架构 §10.1 C1–C7）。
- 模拟器使用**理想**分压与**理想**时基；真板 ADC 误差与中断抖动需实测后回写窗口与阈值。
- `socket://` 引入的传输延迟**不**参与固件时基（固件时基为 `millis`）；集成测试中主机侧的超时断言需据此留出余量，不要试图用 socket 延迟证明硬件时延。
