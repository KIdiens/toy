# 交付说明 — toy/host「无硬件档位」

> 交付物：`toy/`（可换公仔 AI 语音动作玩具 · 主机程序的软件骨架）
> 文档版本：1.0 · 编制：卜宕机（运维工程师）· 日期：2026-09-22
> 依据：`docs/SPEC-host-mvp.md`、`docs/architecture/00-architecture-spec.md` §10.1、`docs/architecture/30-traceability.md`、`references/01-standards/production-readiness-scorecard.md`
> 配套：`docs/DELIVERY.html`（本文的可打印 HTML 版，离线、无外部依赖）

---

## 0. 一句话定位与交付边界

**本交付是「无硬件档位」的完整软件骨架**：在普通电脑上、不接任何实物、不联网的条件下，把「按键 -> 录音 -> 识别 -> 对话 -> 合成 -> 说话 -> 动作 -> 记忆」的编排闭环跑通并可确定性回归。

**它不是可上市的整机成品。** 本机已实测的结论只覆盖**软件可复现**部分（对应开发书第 20 章「样机源代码测试」一栏）；实物项（四轴真实运动、舵机中位、电气急停、看门狗实测时延、音效同步、温升、机械装配、真实云 AI、配网/systemd/OTA、断电时钟、真实儿童内容安全、桩级审核的误拦边界）**按架构规格 §10.1 的 C1–C13 全部保留为「待验证」**。本文档的 C 编号与 `docs/architecture/00-architecture-spec.md` §10.1 一一对应；开放决策编号（OD-x）与 `docs/decisions/OPEN-DECISIONS.md` 一一对应。

> **判定原则（贯穿全文）**：**模拟与格式检查不得替代实物证据**。测试全绿只说明「软件逻辑正确」，不说明「整机就绪」。本阶段生产就绪评级为 **Bronze（最低可上线档，非商业级）**，最低档由短板维度决定，详见第 7 节。

### 已交付 / 未交付 一览

| 项 | 状态 |
|---|---|
| 主机编排程序（状态机 / 音频 / AI 路由 / 动作调度 / SQLite / 网页） | 已交付，`host/` |
| 进程内 MCU 模拟器（复刻固件语义 + 故障注入 + 虚拟时钟 + 回放） | 已交付，`sim/` |
| 确定性桩 AI 后端（零网络出口） | 已交付，`host/ai/stub_provider.py` |
| 空音频后端（无设备可跑全链路） | 已交付，`host/audio/null_backend.py` |
| 网页管理（5 页面 + 关键帧编排器） | 已交付，`host/web/` |
| 自动化测试（unit / contract / integration + 15 份黄金录制） | 已交付，`tests/`，955 passed |
| 真实固件编译烧录 / 真板联调 | **未交付**（无 ESP32，C1/C5） |
| 真实舵机运动 / 机械装配 | **未交付**（无舵机与结构件，C1/C2/C7/C8） |
| 电气急停断电回路 | **未交付**（无常闭触点/继电器，C3） |
| 真实声卡录放 / AEC | **未交付**（无音频硬件，C6） |
| 真实云端 AI（百炼 ASR/LLM/TTS） | **未交付**（本阶段明确不接，C9/C12） |
| 配网 / systemd 自启 / OTA | **未交付**（无 Pi 部署环境，仅设计+目录骨架，C10） |
| 反馈舵机 / RTC / ADC 实测 | **未交付**（C2/C4/C11） |

---

## 1. 交付清单（文件树 + 顶层职责）

交付边界 = 下方文件树（`.gitignore` 已排除运行时产物与一次跑测目录）。共 **207 个文件**，无 `node_modules`、无 `.env`、无测试数据库、无浏览器截图产物。

```
toy/
├── README.md                       # 一句话定位 / 能/不能 / 安装运行测试 / 目录导航 / 已知边界
├── pyproject.toml                  # 依赖与工具配置的唯一硬编码来源
├── .gitignore                      # 运行时产物、缓存、密钥、截图产物排除规则
├── content/                        # 角色配置与动作文件 + 本地音频目录
│   ├── roles/1|2|3/{role.json, actions/*.json}
│   └── audio/.gitkeep              # 运行时音频导出目录（占位说明，内容不入库）
├── data/.gitkeep                   # 运行时数据落点占位（Spec §4；缺它测试 56 报错，见 §9）
├── docs/                           # PRD / SPEC / 架构 / ADR / OpenAPI / 本交付文档
│   ├── PRD-host-mvp.md  SPEC-host-mvp.md  README.md
│   ├── DELIVERY.md  DELIVERY.html
│   ├── api/openapi.yaml
│   ├── architecture/{00-architecture-spec,10-tech-selection-matrix,20-mcu-simulator,30-traceability,40-storage-design}.md
│   └── decisions/{ADR-001..012, OPEN-DECISIONS}.md
├── firmware/motion_controller/motion_controller.ino   # 冻结基线固件源码（只读参考，本阶段不编译）
├── host/                           # 主机程序（分层：web -> adapters -> 业务 -> storage -> config）
│   ├── main.py                     # 入口：只装配，78 行（<100 行门禁）
│   ├── config/                     # 唯一配置加载点（env 覆盖 defaults.toml）
│   ├── core/                       # 确定性核心：显式状态机 / 事件总线 / 时钟 / 异常
│   ├── motion/                     # 串口协议客户端与动作（Serial/Sim/Replay 三实现）
│   ├── audio/                      # 音频抽象层（真 / Null / 文件后端）
│   ├── ai/                         # AI 路由与提供方（Stub/Qwen）+ 儿童内容审核 + 工具白名单
│   ├── tools/                      # 本地工具：天气 / 提醒 / 内容与动作播放
│   ├── roles/                      # 角色注册/校验/切换 + 资源包 zip 安全解包
│   ├── storage/                    # sqlite3 门面 + user_version 迁移 + 四个仓储
│   ├── web/                        # FastAPI 装配 / 路由 / Pydantic / 静态页与设计令牌
│   ├── adapters/                   # 端口到 HTTP/WS 桥接、DTO 转换、运行期装配
│   └── updater/.gitkeep            # OTA 骨架占位（非功能代码，本阶段不做，见 C10）
├── sim/                            # MCU 模拟器：固件状态机复刻 + 故障注入 + TCP 桥 + 回放 CLI
└── tests/                          # unit / contract / integration / fixtures / frontend 自检脚本
```

顶层目录职责一句话：

| 路径 | 职责 |
|---|---|
| `content/` | 角色配置（`role.json`）与关键帧动作文件（`actions/*.json`）；`audio/` 为运行时音频导出目录（占位，内容不入库） |
| `data/` | 运行时数据落点（默认库 `toy.sqlite3` / 日志 / 缓存）；由 `data/.gitkeep` 保留目录、内容不入库，属 Spec §4 结构，**不可删除** |
| `docs/` | 需求、规格、架构、决策与交付文档的唯一真源 |
| `firmware/` | 冻结基线固件源码，仅作协议对齐参考 |
| `host/` | 主机编排程序（业务代码），依赖只能向下、单文件 ≤300 行；`updater/` 为 OTA 骨架占位（非功能代码） |
| `sim/` | MCU 模拟器，独立于 `host/`，以 `MotionLink`/TCP 形态对接业务层 |
| `tests/` | 自动化测试与黄金录制夹具 |

---

## 2. 能达到什么（Spec §9 验收标准覆盖情况）

以下 AC 均为**软件可复现验收**，本机实测全绿（955 passed）。AC-01 / AC-04 / AC-23 / AC-26 由**表驱动/参数化用例**覆盖（测试代码内未逐一标注编号），其余在测试中显式标注。

| AC | 验收摘要（EARS） | 主要覆盖用例 | 状态 |
|---|---|---|---|
| AC-01 | `RECORDING` 时收到表演键 -> 取消录音、删临时录音、离开录音态 | `unit/test_state_machine.py`（`PERFORM_PRESS-RECORDING` 迁移格） | 覆盖（表驱动） |
| AC-02 | `FAULT_LOCKED` 未人工确认 -> 永不自动回 `IDLE` | `unit/test_state_machine.py` | 通过 |
| AC-03 | 任意运动态触发拔出/急停/串口超时 -> 停机并锁定 | `integration/test_client_di.py`、`integration/test_no_hardware_chain.py` | 通过 |
| AC-04 | 模拟器未 `ARM` 时发 `FRAME` -> 返回 `ERR FRAME` 且不产生 PWM | `integration/test_sim_fault_paths.py`（`frame_unarmed-ERR FRAME`） | 覆盖（表驱动） |
| AC-05 | 期望角色 1、识别到角色 2 -> 主机不发 `ARM` | `integration/test_client_di.py` | 通过 |
| AC-06 | 300ms 无有效帧 -> 停机并上报 `FAULT INTERLOCK_OR_TIMEOUT` | `integration/test_client_di.py` | 通过 |
| AC-07 | 末帧后收不到 `SETTLED` -> 2s 内报错停机，`finally` 必发 `STOP` | `integration/test_client_di.py` | 通过 |
| AC-08 | 末帧保持 ≥150ms 再发 `STOP` | `integration/test_client_di.py` | 通过 |
| AC-09 | 1399/1601/NaN/Inf/布尔/字符串 -> 全部抛 `ValueError` | `unit/test_validation.py` | 通过 |
| AC-10 | 时间非严格递增或峰值速度 >300μs/s -> 拒绝 | `unit/test_validation.py` | 通过 |
| AC-11 | 离线预演四轴全程 1400–1600μs 且首末帧 1500 | `unit/test_interpolation.py` | 通过 |
| AC-12 | 已确认记忆写库后关连接重开可读回 | `unit/test_storage.py` | 通过 |
| AC-13 | 删除记忆后新会话上下文不再注入（含历史与摘要缓存） | `unit/test_memory_injection.py`、`unit/test_storage.py` | 通过 |
| AC-14 | 记忆开关关闭时不说「记住」就写库 | `unit/test_storage.py` | 通过 |
| AC-15 | 换角色后旧角色迟到回复不播放，`session_generation` 自增 | `unit/test_role_switch_generation.py`、`integration/test_client_di.py` | 通过 |
| AC-16 | 携带旧 `role_id` 的网页播放请求被拒 | `integration/test_no_hardware_chain.py` | 通过 |
| AC-17 | 未设 `DASHSCOPE_API_KEY` -> 桩后端运行且零网络出口、无致命错误 | `unit/test_ai_offline.py`、`integration/test_no_hardware_chain.py` | 通过 |
| AC-18 | 桩返回「调用 get_weather」但参数非法 -> 本地拒绝且不编造天气 | `unit/test_tool_whitelist.py`、`unit/test_ai_safety_gate.py` | 通过 |
| AC-19 | 输出审核不合格 -> 在合成/播放**之前**拦截 | `unit/test_ai_safety_gate.py` | 通过 |
| AC-20 | 危险现实操作 -> 引导找成年人；索要住址/学校/密码 -> 固定拒绝 | `unit/test_ai_safety_gate.py` | 通过 |
| AC-21 | 正在播放时收到录音请求 -> 先停播再录音（半双工） | `unit/test_audio_pipeline.py` | 通过 |
| AC-22 | 网络线程注入 10s 假请求 -> <100ms 内串口与按键仍响应 | `integration/test_network_does_not_block_serial.py` | 通过 |
| AC-23 | 非法动作包调 `POST /actions/validate` -> 拒绝且不触发硬件动作 | `integration/test_web_mount.py`、`contract/test_motion_parity.py` | 覆盖（表驱动） |
| AC-24 | 已有时间轴播放中收到第二个播放请求 -> 拒绝/替换而非静默叠加 | `integration/test_no_hardware_chain.py` | 通过 |
| AC-25 | 含 `../`/绝对路径/符号链接/超量文件/高压缩比 zip -> 全拒且保留旧版本 | `unit/test_rolepack_guard.py` | 通过 |
| AC-26 | 注入 `BUTTON n` 逐格符合开发书 §5.2 行为表（含未装公仔不启用电机） | `unit/test_state_machine.py`（`test_button_matrix_matches_spec_7_1_1`） | 覆盖（表驱动） |

补充可达能力（本次交付实跑，见第 8 节）：
- 统一响应信封 `{code, data, message}` 在成功/409/422/404 路径均生效；
- 五个页面与 `/static/*` 资源可访问（HTTP 200）；
- `POST /api/v1/actions/play` 走通完整闭环：模拟器收到 `ARM` -> 206 帧 `FRAME` -> `SETTLED` -> `STOP`，动作结束回 `IDLE`；
- 拔出公仔 -> 抢占式 `FAULT_LOCKED`（fault=`INTERLOCK`），人工确认后回 `IDLE` 且 `session_generation` 自增。

---

## 3. 不能达到什么（Spec §10.1：C1–C13 全部 13 项）

| # | 开发书要求 | 为何本阶段无法验收 | 本阶段可做的替代（已做） | 何时可验收 |
|---|---|---|---|---|
| **C1** | 四轴动作无卡死 / 无碰撞 / 复位 | 无舵机与机械结构件，**无法观测真实运动**；模拟器只能给出脉宽序列 | 协议级验证帧序列、限位、看门狗停机、末帧保持 | 样机 A 到货 |
| **C2** | 舵机中位校准与「实际位置」 | PWM 舵机**无位置反馈**，固件 `SETTLED` 只证明指令到达，不证明机械到位 | 文档与界面统一标注为「目标位置」，禁止显示为「实际角度」 | 反馈舵机/传感器到位 |
| **C3** | 急停**电气断电** | 需真实常闭触点/继电器切断舵机支路 | 仅验证「急停健康信号 -> 停机」的软件逻辑 | 电气件到位 |
| **C4** | ADC 角色识别窗口（150–450/850–1250/1450–1900 mV） | 依赖真实 `analogReadMilliVolts` 与 3V3 精度，**未实测** | 模拟器用理想分压值，仅验证「窗口映射 -> 稳定 200ms -> 联锁」逻辑 | 万用表逐档实测后修正窗口 |
| **C5** | 300ms 看门狗实际时延、20ms PWM 周期 | 精确时延需逻辑分析仪/示波器，**肉眼不能证明** | 模拟器验证超时的**逻辑**行为 | 台架实测记录 |
| **C6** | 录放音质、动作<->音效同步 <100ms | 需 USB 音频设备；Windows(WASAPI) 与 Pi(ALSA) 延迟不同 | NullAudio 验证时序逻辑；同步用统一单调时钟采样 | 声卡到位后测 |
| **C7** | 温升、连续 ≥2h 混合运行 | 需实物与热环境 | 设计层面保证无忙等/无泄漏 | 整机测试 |
| **C8** | 机械耦合、插拔 100 次、尺寸公差、CAD | 无机械件；开发书明确未提供可制造 CAD | 只定义接口与角色包格式 | 样机 B |
| **C9** | 真实云 AI 准确率/延迟/音色/费用/限流 | 本阶段不接百炼 | 桩 + 契约测试；错误分类（401/403/404/429）已编码 | 开通 Key 后跑同一契约测试 |
| **C10** | 配网、systemd 自启、OTA 分层回滚 | 需 Pi + 真实网络 + 可写 SD 卡 | 只交付设计 + 目录骨架（`host/updater/` 空占位）+ 回滚流程文档 | 硬件到位后 |
| **C11** | 断电后时钟有效性 -> 绝对时间提醒 | Pi 4B 无 RTC；需冷启动断网复现 | 设计 `TIME_UNKNOWN` 状态：暂停绝对提醒并提示校时 | 可选加 RTC |
| **C12** | 真实儿童内容安全 | 需真实模型边界问题集 | 桩测「出声前审核」流程与固定拒绝模板 | 模型接入后 |
| **C13** | 桩级输出审核的**误拦**边界 | 桩守卫基于词表 + 前后置锚，**无法穷举**所有合法叙事句式；真实模型接入前只能用桩 | 已测得**很窄**的边界：第三人称**具体人名** + 「已经」 + 完成收尾 -> **拦**；纯代词「他」 -> **放行**（详见第 4.1 节） | 随 C12 一并关闭（模型接入后以真实审核替换） |

> **红线**：以上 13 项**任何一项都不得用「模拟结果」或「格式检查」结案**。测试通过不等价于实物通过；`SETTLED` 不等于机械到位；软件 `STOP` 不等于电气断电。本阶段对这 13 项的唯一合法表述是「**待验证**」。

---

## 4. 已知边界（必须明示）

### 4.1 内容安全守卫是**桩级**：误拦边界（C13）与漏放边界（OD-8）

`host/ai/safety.py` 的「越界声称」检测基于词表 + 前后置锚，**无法穷举**所有合法叙事句式。这构成两条互补的已知边界，分别登记在架构规格与开放决策中——本节即 **Spec §10.1 的 C13**（误拦）与 `docs/decisions/OPEN-DECISIONS.md` 的 **OD-8**（漏放）的展开说明。实测证据来自 QA 复验的 held-out 集。

#### 4.1.1 误拦边界 —— 对应 **C13**（fail-safe 方向，非安全漏洞）

第三人称**具体人名** + 「已经」 + 完成收尾 -> 拦截。合法叙事句被拦下：

| 输入 | 期望 | 实际 | 判定 |
|---|---|---|---|
| `小熊已经拿到了一颗糖` | ALLOW | BLOCK（声称已完成现实操作: 已经拿到） | 误拦 |
| `小狗已经打开了门` | ALLOW | BLOCK | 误拦 |
| `小兔子已经关好门了` | ALLOW | BLOCK | 误拦 |
| `奶奶已经买好菜了` | ALLOW | BLOCK | 误拦 |
| `老师已经寄出了信` | ALLOW | BLOCK | 误拦 |
| `他已经关好窗户啦` | ALLOW | ALLOW（纯代词「他」，无具体人名） | 正确放行 |

#### 4.1.2 漏放边界 —— 对应 **OD-8**（advisory，不重开 B3）

第一人称 + 替/帮 + 动作 + 「了」，但**不含「把/将」**，后置锚未命中 -> 放行：

| 输入 | 期望 | 实际 | 判定 |
|---|---|---|---|
| `我替你转账了` | BLOCK | ALLOW（无「把/将」，后置锚未命中） | 漏放 |

#### 4.1.3 怎么理解这两条边界（写给接手的人与决策者）

- **C13 的危害方向是安全的（fail-safe）**：玩具会**偶发拒说**个别合法的叙事句（例如讲一个「小熊已经拿到糖」的故事），体验上有噪声，但**不会漏放**不当内容，也**不会真去做任何现实动作**（它没有能改变物理世界的能力）。属「桩守卫无法穷举」的**已知成本**，边界**很窄（仅限具体人名）**，按 C12 接受；AI 模型接入后以真实审核替换（随 C12/C13 一并关闭）。
- **OD-8 是 C13 的互补方向**：它不是本次引入的缺陷，而是锚设计**既有的覆盖边界**；处置是「收紧后置锚时增补『我 + 替/帮 + 动作 + 了』分支并补回归用例」，本阶段按 advisory 登记，**不重开 B3 阻断**。
- **仍是桩级**：两条判定都基于本地句式规则，非真实模型审核。真实内容安全的验收（C12）必须等真实模型接入后，用真实边界问题集重跑。
- **不做的事**：不为「把 C13 的误拦压到零」而放宽规则——放宽会同时扩大 OD-8 这类**漏放**面，方向错误。

### 4.2 开放决策登记（Open Decisions）导航

除上述 C13 / OD-8 外，本阶段还有若干**主动不做 / 无法在本阶段关闭**的事项，统一登记在 `docs/decisions/OPEN-DECISIONS.md`（**OD-1..OD-8**，不混入已 Accepted 的 ADR，每项都有触发条件与归属）。摘要：

| ID | 事项 | 本阶段处置 |
|---|---|---|
| OD-1 | 固件协议增补（版本号 / CRC / 每帧 ACK / 故障码） | 不做（需改固件，无真板无法验证；对应 C10 一侧） |
| OD-2 | 帧序号回绕规则（32 位 `unsigned long`） | 不做·无优先级（`2³² × 20ms ≈ 2.7 年`才回绕） |
| OD-3 | 主机侧限位真源与固件 1400–1600μs 的长期收敛 | 实质分歧已定死（主机侧 = 角色配置 `min_us`/`max_us`），更大行程方案待定 |
| OD-4 | `previousSeq` 重置 / 重放防护规则的正式化 | 不做（Lead 已裁定为有意的重放防护） |
| OD-5 | 图标尺寸令牌化（`--icon-*`） | 不做（暂不纳入门禁图标尺寸） |
| OD-6 | 前端 TS 类型声明 / MSW Mock 生成 | 不做（与 ADR-007「无构建链」冲突） |
| OD-7 | 真实云 AI（百炼）接入相关参数 | 不做（本阶段不接云端；对应 C9） |
| OD-8 | 桩级输出审核：第一人称「替/帮 + 动作 + 了」未被后置锚拦截 | advisory·不重开 B3（见 4.1.2） |

> 逐条明细（现状 / 为何不做 / 触发条件 / 归属）见 `docs/decisions/OPEN-DECISIONS.md`；本文档不重复抄录。

### 4.3 其它已知缺口

| 缺口 | 说明 | 影响面 |
|---|---|---|
| 图标尺寸未令牌化 | `DESIGN.md` 给固定阶梯 16/20/24px（16px 描边 1.75），未落到 `design-tokens.css` 的 `--icon-*`；另见 OD-5 | 门禁无法自动校验图标尺寸；当前靠设计规范约束 |
| `host/updater/` 空目录 | OTA 骨架占位，本阶段不做（C10）；已加 `.gitkeep` 并写明「非功能代码、勿理解为已实现」 | 无运行时影响 |
| `content/audio/` 空目录 | 本地音频为运行时导出，不入库；已加 `.gitkeep` 写明职责 | 目录保持存在，`ContentCatalog` 容忍缺内容 |
| `status.action.progress_ms` 播放中保持 0 | 离线档位由前端本地插值驱动 playhead（见 `host/web/design/pages/02-editor.md`）；进度值在动作结束回写 | 编排器在线时依赖事件流；离线时用本地插值，属既定设计 |

---

## 5. 怎么跑起来（四条命令，逐条给预期输出）

前置：CPython ≥3.11（建议 3.13），在 `toy/` 目录下操作。

> **解包自检（先做，10 秒）**：交付包自带 `data/` 目录（Spec §4 运行时数据落点）。缺失会让 `pytest --basetemp=data/...` 在 setup 阶段报 56 个 `FileNotFoundError`（见 §9）。命令与说明见 `README.md` 的「环境自检」一节：
>
> ```bash
> python -c "import os,sys; need=['data','data/.gitkeep','host','sim','content/audio','tests','docs']; miss=[p for p in need if not os.path.exists(p)]; print('self-check OK: 目录结构完整' if not miss else 'self-check FAIL, missing: '+', '.join(miss)); sys.exit(bool(miss))"
> ```
>
> 输出 `self-check OK` 后再继续。

### 5.1 安装

```bash
cd toy
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"       # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux / Raspberry Pi OS
```

预期：`Successfully installed toy-host-0.1.0 ...`（含 fastapi / uvicorn / pydantic / pyserial / sounddevice / soundfile / requests / pytest / ruff）。

### 5.2 启动（无硬件档位）

```bash
python -m host.main --simulate
```

预期输出：

```
INFO host: active role -> 1 (1.0.0)
INFO host.main: 主机服务就绪：http://127.0.0.1:8000（simulate=True，AI=stub）
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

浏览器打开 `http://127.0.0.1:8000/`（状态面板）、`/editor`（编排器）、`/memories`、`/reminders`、`/settings`。
退出用 `Ctrl+C`。可用 `--port` / `--db` / `--log-level` 覆盖默认值。

### 5.3 测试

```bash
pytest -q
```

预期：`955 passed`。

```bash
ruff check .
```

预期：`All checks passed!`。

### 5.4 黄金回放比对

```bash
python -m sim --replay tests/fixtures/sessions/*.jsonl --diff
```

预期：15 行 `RESULT: PASS（<场景>.jsonl：N 行逐行一致，含时间戳）`，退出码 0。

---

## 6. 交接要点

### 6.1 换真硬件：改哪里

三个适配器（`docs/SPEC-host-mvp.md` §2.3）各自独立，换真件只动对应一处；契约测试对真实现与替身**跑同一套断言**，因此替换后可立即用同一批测试回归。

| 适配器 | 真实现（文件） | 本阶段替身（文件） | 切换方式 |
|---|---|---|---|
| `MotionTransport`（动作链路） | `host/motion/serial_link.py`（pyserial，支持 `COM*` / `/dev/serial/by-id/*` / `socket://`） | `host/motion/sim_link.py`（进程内）+ `sim/controller.py`（复刻 `motion_controller.ino`）；TCP 形态 `sim/tcp_bridge.py` | 启动时不加 `--simulate`，并设 `MOTION_PORT=COM5`（或 `/dev/serial/by-id/...`）。默认值是 `socket://127.0.0.1:5555` |
| `ChatBackend`（AI 链路） | `host/ai/qwen_provider.py`（真实百炼 REST） | `host/ai/stub_provider.py`（确定性桩，零网络出口） | 见 6.2 |
| `AudioIo`（音频链路） | `host/audio/sd_backend.py`（sounddevice + soundfile） | `host/audio/null_backend.py`（无设备定长虚拟录放） | 见 6.3 |

替换后需用**同一批契约测试**回归：动作链路 `tests/contract/test_motion_parity.py` + `tests/integration/test_client_di.py`；AI 链路 `tests/unit/test_qwen_provider.py`；音频链路 `tests/unit/test_audio_pipeline.py`。

### 6.2 接真云端：改哪里（一处切换）

- **唯一开关**：环境变量 `AI_PROVIDER`（取值 `stub` | `qwen`），默认 `stub`。
- 选择逻辑集中在 `host/config/settings.py`（读 `AI_PROVIDER` + `DASHSCOPE_API_KEY`）与 `host/adapters/runtime.py::_build_ai`（按 provider 选真/桩；Qwen 初始化失败一律回落桩，不致命）。
- 需要密钥时只设环境变量 `DASHSCOPE_API_KEY`，**不落盘、不进日志**（`Settings.redacted()` 保证不回显）。
- 未设 Key 时 `AI_PROVIDER` 自动落 `stub`（AC-17）。

```bash
# 接真云端（示例）
export AI_PROVIDER=qwen
export DASHSCOPE_API_KEY=<你的 Key>
python -m host.main --simulate
```

### 6.3 接真音频硬件

音频后端当前在装配处以 `NullAudioBackend()` 固定挂载（`host/adapters/runtime.py`）。接入真声卡时，把该处改为 `host/audio/sd_backend.py` 的实现（`sounddevice` + `soundfile`），并确保已安装这两个依赖（`pyproject.toml` 已声明）。这是**一处装配改动 + 依赖到位**，业务代码无需变更。

### 6.4 部署形态与成本（运维视角）

本阶段无部署环境（C10 未交付）。以「无硬件档位」的形态，交付物在**单机 PC** 上即可运行，无外部依赖、无云资源、无域名/SSL，**运行成本为零**。
若后续要上树莓派（阶段 C），参考 `references/cost-models/development-costs.md` 的隐性成本清单（服务器/云服务 ¥100–500/月、可选域名+SSL+CDN ¥100–300/年、监控告警）与 `references/architecture/mvp-stack.md` 的部署方案矩阵；本阶段**不选定**任何部署平台，Spec 未锁定即不实施。

---

## 7. 生产就绪清单（7 维 × 3 档记分卡）

> 依据 `references/01-standards/production-readiness-scorecard.md`：**总档取各维最低档**；商业级最低线是 Silver。
> 当前评级：**Bronze**（7 维中「测试+回归」「契约」达 Bronze，其余维度因本阶段无部署环境 / 无实物 / 无观测目标而停留在 Bronze 或未验证）。
> **不做美化**：未验证的写「未验证」，不写「通过」。

| 维度 | 档位 | 达标证据 | 未达 Silver 的差距（诚实列出） |
|---|---|---|---|
| **测试 + 回归** | **Bronze** | 全量 `pytest` **955 passed, rc=0**（本机实测）；`ruff check .` 全仓 **All checks passed**；15 份黄金录制回放 **15/15 逐行一致**；回归集（`tests/fixtures/sessions/`）已进仓库 | 未做**影响图定向覆盖**、无**回归率**量化指标、回归集未接入 CI 门禁（无 CI 环境）、未做 pass^k / 变异测试 |
| **契约** | **Bronze** | `docs/api/openapi.yaml`（OpenAPI 3.0.3，14 paths / 14 schemas）为前后端唯一依据；`tests/contract/` 含动作校验一致性（对齐冻结基线，503 例）、存储 schema、设计契约校验；统一错误信封已实现 | 无**契约先行门禁**（契约破坏不会在 CI 变红）、无**版本与弃用策略**闭环、前后端未做机器化对照断言 |
| **安全** | **Bronze** | 密钥仅走环境变量、`Settings.redacted()` 不回显、不进日志；`/status` 及所有端点不返回密钥；SQLite 开外键 + 参数绑定；资源包导入防路径穿越/符号链接/压缩炸弹（15 例）；工具白名单禁任意 URL/命令/脉宽；默认只绑 `127.0.0.1` | 无鉴权/RBAC（本阶段单机回环，未开放局域网）、无限流、无依赖存在性核验、无 SAST/供应链扫描门 |
| **无障碍** | **Bronze（未验证）** | 无——本阶段未做任何无障碍验收 | **未验证**：核心流键盘可达性、对比度/焦点/语义 AA、自动扫描均**未做**。页面可键盘操作是设计意图，但**无证据**，不写「通过」 |
| **性能** | **Bronze（未验证）** | 仅结构性证据：无 N+1 全量加载、动作按单调时钟采样（禁止逐帧 sleep 累加）、离线预期为常态 | **未验证**：管理页首屏 <3s、状态切换 p95 <500ms 均**未实测**；无分页/索引/超时压测；无容量验证 |
| **可观测** | **Bronze** | 结构化日志（`logging`，脱敏：不记密钥、不记儿童原始输入全文）；故障与状态经事件流可观测；异常分类（Config/Interlock/Timeout/Provider/Storage）齐全；`/api/v1/status` 暴露 state/fault | 无**结构化日志字段规范**、无**关键指标/SLI/SLO**、无告警、无追踪、无错误预算；本阶段无部署环境故无采集后端 |
| **发布安全** | **Bronze（未验证）** | 可回退性由版本管理承载（源码可回滚）；无破坏性迁移（`user_version` 前向迁移） | **未验证**：无部署制品/镜像、无**渐进发布**、**回滚预案未演练**、无自动回滚触发、无特性开关 |

**总档 = Bronze**（最低档决定）。**离 Silver 的短板优先级**（补齐顺序建议）：

1. **无障碍**（零证据 -> 差距最大）：补键盘可达性走查 + 对比度/焦点/语义 AA 自动扫描，产出 0 阻断报告。
2. **性能**：补管理页首屏与状态切换 p95 的实测数据（本机可测，不依赖实物）。
3. **可观测**：定义结构化日志字段 + 最小 SLI（如 `/status` 可用率、动作失败率）。
4. **发布安全**：出部署制品并演练一次回滚。
5. **测试+回归 / 契约**：接入 CI，把回归集与契约一致性变成硬门禁。

> 说明：本阶段交付目标档**未声明为商业级**（无商业生产计划），因此未达 Silver 属**预期**，不构成放行阻断；但任何「已生产就绪」的表述都**不成立**。

---

## 8. 启动与验证实测输出（本次交付实跑）

环境：Windows，CPython 3.13.14。启动命令：

```
PYTHONPATH=F:/AI智能底座玩具/toy  python -m host.main --simulate --port 8123 --db <scratch>/verify.sqlite3
```

服务启动日志：

```
2026-09-22 11:58:50,110 INFO host: active role -> 1 (1.0.0)
2026-09-22 11:58:50,115 INFO host.main: 主机服务就绪：http://127.0.0.1:8123（simulate=True，AI=stub）
INFO:     Started server process [49268]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8123 (Press CTRL+C to quit)
```

`GET /api/v1/status`（统一信封）：

```
HTTP 200  (10.3 ms)
{"code":0,"data":{"state":"IDLE","mcu_armed":false,"docked":true,"estop_healthy":true,
"role_id":1,"session_generation":1,"network_online":false,"fault":null,"action":null},"message":""}
```

五个页面 + 静态资源（注：探针以 `ProxyHandler({})` 直连回环，绕过本机 `http_proxy`，否则被代理劫持返回 404）：

```
HTTP 200     92.4 ms     4011 B  /
HTTP 200      2.7 ms     4765 B  /editor
HTTP 200      2.4 ms     2782 B  /memories
HTTP 200      2.2 ms     1992 B  /reminders
HTTP 200     19.8 ms     4174 B  /settings
HTTP 200      2.6 ms     2190 B  /static/app.js
HTTP 200      2.4 ms    11923 B  /static/styles.css
HTTP 200      2.4 ms     4011 B  /static/index.html
```

`POST /api/v1/actions/play`（带 `request_id` + 预期 `role_id`）：

```
HTTP 200  (2.7 ms)
{"code":0,"data":{"scheduled":true,"action_id":"wave","seq":0},"message":""}
```

完整动作闭环（`wave`，4s 动作 + 2s boot_wait）：

```
play -> 200 {"code":0,"data":{"scheduled":true,"action_id":"wave","seq":0},"message":""}
saw PERFORMING=True  saw mcu_armed=True=True
回到 IDLE 用时 6.32s（含 boot_wait 2s + wave 4000ms）
final status: {"state":"IDLE","mcu_armed":false,"docked":true,"estop_healthy":true,
"role_id":1,"session_generation":3,"network_online":false,"fault":null,
"action":{"action_id":"wave","seq":206,"progress_ms":4000}}
```

> `seq=206` 表示本次播放向模拟器发出了 **206 帧 `FRAME`**，`progress_ms=4000` 与动作时长一致，证明 `ARM -> FRAME×206 -> SETTLED -> STOP` 全链路在模拟器侧被接受并正常收尾。

第二个播放请求（AC-24，不静默叠加）：

```
HTTP 409  (1.8 ms)
{"code":40900,"data":null,"message":"已有动作在播放，默认拒绝叠加（可用 replace_current 显式替换）"}
```

拔出公仔（抢占式故障锁定）与人工确认解除：

```
undock -> 200 {"state":"FAULT_LOCKED","docked":false,"fault":{"code":"INTERLOCK","detail":"公仔被拔出（到位信号丢失）"}, ...}
clear  -> 200 {"cleared":true,"state":"IDLE"}
（clear 后 session_generation 由 3 自增为 4）
```

参数校验与未知路由（统一信封）：

```
POST /actions/stop 缺 request_id -> HTTP 422 {"code":42201,...,"message":"请求参数校验失败：request_id: Field required"}
GET  /api/v1/nope              -> HTTP 404 {"code":40400,...,"message":"Not Found"}
```

服务生命周期确认：验证完成后以 `taskkill /F /PID 49268` 停止；复验 `http://127.0.0.1:8123/api/v1/status` 返回 `WinError 10061 目标计算机积极拒绝`，`tasklist` 中 PID 49268 已不存在，**无遗留后台进程**。

门禁自检（本次实跑）：

```
ruff check .            -> All checks passed!            (rc=0)
pytest (全量)            -> 955 passed in 17.48s          (rc=0)
python -m sim --replay ... --diff -> 15/15 PASS           (rc=0)
host 下 .py 超过 300 行者 -> 无；host/main.py = 78 行（<100）
```

---

## 9. 交付包边界与清理记录

`toy/` 已清理为可交接形态：

| 处理项 | 处理方式 |
|---|---|
| `_lc.txt`、`_r.txt`（根目录临时跑测输出） | 已删除 |
| `_verify_shots/`、`_verify_shots_qa2/`（浏览器验证截图与 Chromium 用户目录） | 已移出交付树（可由 `tests/frontend/verify_browser.py` 重新生成） |
| `data/` 下的运行时内容（pytest basetemp、测试 SQLite、QA 临时证据） | **内容**已清空（`.gitignore` 以 `data/*` 排除）；**目录本身保留**（`data/.gitkeep` 占位）——见下方说明 |
| `.pytest_cache/`、`.ruff_cache/` | 已删除 |
| `host/.gitkeep`（历史占位，`host/` 已非空） | 已删除 |
| `.gitignore` | 补 `_verify_shots*/`、`.ruff_cache/`、`*.sqlite3*`、`_scratch/`，覆盖全部运行时产物；并把 `data/` 规则改为 `data/*` + `!data/.gitkeep`，让 `data/` 目录在 clone 后仍存在 |

`_verify_shots*/` 属一次性产物，`.gitignore` 已排除，可由 `tests/frontend/verify_browser.py` 重新生成。`data/` 下的**内容**（basetemp、测试库、日志）同样是运行时产物、`.gitignore` 已排除；但 **`data/` 目录本身必须留在交付树内**——它是 Spec §4 目录结构的组成部分（`pyproject.toml` 的 ruff `extend-exclude` 亦把 `data` 列为已知目录）。目录由 `data/.gitkeep` 占位保留。

> 本次修复教训：pytest 收到 `--basetemp=data/_final_v1` 时以 `parents=False` 调 `mkdir`，`data/` 不存在即在 setup 阶段报 **56 个 `FileNotFoundError`**（899 passed / 56 errors）。因此 `data/` **不是**「会自动重建」的可丢弃目录，不能当临时产物移出。

### 交付前发现的问题与处置（含已修订项）

| 项 | 现象 | 处置 |
|---|---|---|
| `pyproject.toml` 与实测环境版本不符 | 曾声明 `uvicorn>=0.34,<0.53`，而本机实测安装 `uvicorn 0.53.0`（不满足上界）；`fastapi 0.128.8` / `pydantic 2.13.5` / `pyserial 3.5` 均满足 | **已修订（2026-09-22，Lead 裁决）**：上界改为 `>=0.34,<0.54`（跟上 minor），不回落环境到 0.52；理由：0.53.0 上全量回归全绿，「依赖唯一真源」与实测环境不一致比版本号本身更成问题。ADR-006 已同步记录该修订 |
| `sounddevice` / `soundfile` 未安装 | `pyproject.toml` 声明为必需依赖，本机未装（无硬件档位走 Null 后端，导入为延迟加载，故测试全绿） | 属环境事实，非缺陷。无硬件档位可不装；`pip install -e .` 会一并装上。**不处理** |
| `host/updater/` 空目录 | OTA 骨架占位（C10 未交付） | **已处理**：加 `.gitkeep` 写明「非功能代码、勿理解为已实现」；`README.md` 目录导航已补职责说明 |
| `content/audio/` 空目录 | 本地音频为运行时导出 | **已处理**：加 `.gitkeep`；`README.md` 目录导航已补职责说明 |
| `data/` 目录被误当临时产物移出（**交付阻塞**，2026-09-22 Lead 复验发现） | 全量测试由 955 passed 退化为 **899 passed / 56 errors**，报 `FileNotFoundError: ...toy\data\_final_v1`（pytest 建 basetemp 不创建父目录） | **已修复**：恢复 `data/` + 加 `data/.gitkeep`；`.gitignore` 由 `data/` 改为 `data/*` + `!data/.gitkeep`（否则整目录被排除后 `!` 例外不生效）；`README.md` 增补环境自检命令；对 Spec §4 目录树逐条核对（78 项，仅 `host/web/static/vendor/lucide-icons.js` 与 §4 草案文件名 `lucide.min.js` 不同，属 ADR-007「无 Node 构建链、Shadow DOM 内联 SVG」的既定实现，非缺失） |
