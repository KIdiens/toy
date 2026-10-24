# toy/host 技术选型对比矩阵

> 状态：Accepted（2026-09-21）· 负责人：高见远（首席架构师）
> 版本号来源：均为 2026-09-21 联网核对官方发布页/PyPI/官方文档所得；每条结论对应 `docs/decisions/ADR-XXX.md`。
> 评分：5 分制。权重按「学习成本 / 生态成熟度 / 部署成本 / 扩展性 / 团队熟悉度」综合。
> 约束前提：Windows 开发 → 树莓派 4B 部署；本阶段**无硬件**（ESP32/舵机/USB 音频/独立电源均未采购）、**不接阿里云百炼**。所有选型必须能在无硬件条件跑通并被确定性测试。

## 0. 选型结论速查（锁定表）

| # | 决策项 | 锁定方案 | 版本锚定 | ADR |
|---|--------|----------|----------|-----|
| 1 | 主机语言/运行时 | Python + venv | CPython **3.13**（部署端 Raspberry Pi OS Trixie 自带 3.13；地板 3.11） | ADR-001 |
| 2 | 状态机实现 | 自研显式状态表（数据驱动 transition table） | 无第三方依赖 | ADR-002 |
| 3 | 并发模型 | 领域线程 + 线程安全队列 + 单线程确定性核心 | 标准库 `threading` / `queue` | ADR-003 |
| 4 | 串口抽象 | `MotionLink` Protocol + Serial/Sim/Replay 三实现 | **pyserial 3.5** | ADR-004 |
| 5 | 本地数据库 | 标准库 `sqlite3` 门面 + `user_version` 版本化迁移 | 标准库（无依赖） | ADR-005 |
| 6 | 网页后端 | FastAPI + uvicorn（同源托管静态页） | **FastAPI 0.128.0** / **uvicorn 0.53.x** | ADR-006 |
| 7 | 前端方案 | 无框架原生 ES 模块 + Web Components，无 Node 构建 | 浏览器原生（无 npm 依赖） | ADR-007 |
| 8 | 音频后端 | sounddevice + soundfile + Null 后端可切换 | **sounddevice 0.5.6** / **soundfile 0.14.0** | ADR-008 |
| 9 | 图标库（P0 锁定） | **Lucide**，全项目唯一图标库 | **lucide 1.x**（ISC） | ADR-009 |
| 10 | 云端 AI 适配层 | Provider Protocol + Stub/Qwen 双实现，配置切换 | Stub（本阶段）/ Qwen REST | ADR-010 |
| 11 | 无硬件验证 | MCU 模拟器（3 传输 + 虚拟时钟 + 录制回放） | 自研，复刻 `motion_controller.ino` | ADR-011 |
| 12 | 颜色/设计令牌 | 语义化 CSS 变量，禁止硬编码色值 | 自研令牌契约 | ADR-012 |

---

## 1. 主机程序语言与运行时

| 方案 | 实际版本 | 学习成本 | 生态 | 部署成本 | 与现有代码复用 | 综合 |
|------|----------|:---:|:---:|:---:|---|:---:|
| **Python + venv** | CPython 3.13.15（3.13 末位补丁）/ Raspberry Pi OS Trixie 自带 3.13；Bookworm 为 3.11.2 | 5 | 5 | 5 | 满分：`motion.py`/`cloud_chat.py` 已是 Python，pyserial/sounddevice/sqlite3 全在一等公民位 | **5** |
| Node.js 22 + TypeScript | Node 22 LTS | 4 | 5 | 3 | 需重写全部现有逻辑；串口（serialport）与音频（naudiodon/PortAudio 绑定）在 aarch64 上有原生编译负担 | 3 |
| Go 1.24 | Go 1.24 | 2 | 4 | 5 | 零复用；PortAudio 走 cgo，交叉编译到 Pi 需额外工具链；开发书附录 A 与示例均为 Python | 2.5 |

**结论**：Python **3.13**。理由：①开发书附录 A 与既有 `software/motion.py`、`software/cloud_chat.py` 均为 Python，重写为零收益高风险；②`sqlite3` 是标准库，`pyserial`/`sounddevice`/`soundfile` 都提供 Windows 与 linux-aarch64 两边的官方 wheel；③Raspberry Pi OS **Trixie**（Debian 13）默认 Python 3.13，**Windows 开发与 Pi 部署同版本**，避免「开发 3.13、部署 3.11」的行为漂移；4.0 需要 `python3-venv`（Bookworm/Trixie 的 PEP 668 externally-managed 环境强制走 venv）。地板定 3.11 仅覆盖 `Bookworm` 遗留卡。详见 ADR-001。

## 2. 状态机实现方式

状态集来自开发书 §7.1：自检 / 空闲 / 录音 / 思考(识别+对话+合成) / 说话 / 表演 / 故障锁定，共 7 个状态。

| 方案 | 实际版本 | 学习成本 | 可测试性 | 安全语义可控性 | 依赖风险 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|:---:|
| **自研显式状态表** | 无依赖，约 120 行 | 5 | 5（表即数据，可穷举断言非法迁移） | 5（`can_fire`/`fire` 显式返回拒绝原因，便于「任意运动态→故障」这类抢占式迁移） | 5（无供应链面） | **5** |
| transitions | 0.9.3（2025-07-02，MIT） | 3 | 4（回调解析与「隐式方法注入」使测试需理解其内部约定） | 3（`conditions`+多条件顺序敏感，抢占式迁移要绕） | 4（成熟但维护者单一） | 3.5 |
| python-statemachine | 3.2.1（2026-08-01，MIT） | 4 | 5（`StateChart` 原生 async、异常→`error.execution`、Mermaid 导出） | 5（复合态/历史态/并行域齐全） | 4（3.x 较新，API 在 3.0 有一次大改） | 4 |

**结论**：MVP 用**自研显式状态表**。7 状态、约 14 条迁移的规模下，一张 `{trigger, source, dest, guard, action}` 的列表就是最好的规格文档，且能让「非法迁移」以数据形式被单元测试穷举。**升级触发条件**：状态数 > 12 或出现并行域/历史态需求时，迁移到 `python-statemachine 3.2.1`（其 `StateChart` 与我们的显式表可 1:1 映射，迁移成本低）。不选 `transitions`：其方法注入与条件顺序敏感特性，在「安全联锁」这种要求确定性的场景里增加了隐式面。详见 ADR-002。

## 3. 并发模型

硬需求：**网络请求不得阻塞 MCU 串口通信与按键响应**（开发书 §7 明文）。

| 方案 | 实际版本 | 隔离性（网络卡顿不影响串口/按键） | 与现有阻塞代码复用 | 确定性测试 | 复杂度 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|:---:|
| **领域线程 + 队列 + 单线程确定性核心** | 标准库 `threading`/`queue` | 5（串口线程与网络线程物理隔离，互不抢占） | 5（`pyserial` 阻塞读、`sounddevice` 阻塞回调原样可用） | 5（核心为单线程 actor，测试注入 VirtualClock 同步驱动，无事件循环） | 4 | **5** |
| asyncio 单事件循环 + `to_thread` | 标准库 | 4（阻塞调用仍要下沉线程，否则一个 `requests.post` 就卡住整个循环） | 2（需把阻塞代码逐处改造） | 4（需 `asyncio` 测试运行器，时序断言较绕） | 3 | 3.5 |
| 多进程（串口独立进程） | 标准库 `multiprocessing` | 5 | 2（跨进程共享状态/锁复杂，调试成本高） | 3（跨进程时序不可控） | 2 | 2.5 |

**补充**：`pyserial-asyncio` 虽存在，但最后发布 0.6（2021-09）、仓库自 2023 末休眠，且仅覆盖 POSIX；不引入。音频采集/播放在 asyncio 下也要下沉线程，故 asyncio 方案并不减少线程。**结论**：`threading` + 队列 + 单线程确定性核心（详见 ADR-003）。FastAPI 自带的 asyncio 循环只负责 HTTP/WebSocket，经线程安全队列 + `run_coroutine_threadsafe` 与核心通信。

## 4. 串口抽象层

需求：真实串口 / MCU 模拟器 / 录制回放 **三来源互换**，且**不改调用方代码**。

| 方案 | 实际版本 | 三来源互换 | 故障注入能力 | 依赖 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|
| **自研 `MotionLink` Protocol + 3 实现** | 自研 | 5 | 5（Sim 实现可调度拔线/急停/换角色/断流） | 仅 pyserial 3.5 | **5** |
| pyserial `loop://` / `socket://` URL | pyserial 3.5 | 3（`loop://` 仅回环，无法注入故障与状态机） | 1 | pyserial | 2 |
| pyserial-asyncio | 0.6（2021，休眠） | 2 | 1 | pyserial + 休眠库 | 1.5 |

**结论**：自研 `MotionLink` Protocol（`open/close/write_line/read_lines/reset_input_buffer`）。关键复用技巧：模拟器以 **TCP 服务端** 暴露，`SerialLink` 通过 pyserial 原生 `serial_for_url("socket://127.0.0.1:5555")` 连接——**同一份 `SerialLink` 代码即可对接真实 USB 与模拟器**。故障注入与虚拟时钟由进程内 `SimLink` 承载（单元测试用），回放由 `ReplayLink`（JSONL 录音/回放 + 黄金比对）承载。详见 ADR-004 与 `20-mcu-simulator.md`。

## 5. 本地数据库（SQLite 封装）

| 方案 | 实际版本 | 迁移/回滚能力 | 依赖体积 | 与 §11 schema 契合 | 反过度设计 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|:---:|
| **标准库 `sqlite3` 门面 + `user_version` 迁移** | 标准库 | 4（前向迁移 + 备份/还原脚本，可测） | 0 | 5（§11 给的就是裸 SQL：`profiles`/`memories` + 索引） | 5 | **5** |
| SQLAlchemy 2.0 | 2.0.54（2026-09-15，MIT） | 5（配 Alembic） | 约 41 MB 装好体积 | 4（需把 §11 显式外键/软删除映射成 ORM） | 2（4 张表上 ORM 属过剩） | 3.5 |
| SQLModel / peewee | SQLModel 0.0.x / peewee 3.17 | 3 | 中 | 3 | 2 | 2.5 |

**结论**：标准库 `sqlite3` + 显式 SQL + 参数绑定 + `PRAGMA foreign_keys=ON` + `PRAGMA user_version` 驱动的 `migrations/NNNN_*.sql` 前向迁移目录。§11 的表结构本就是裸 DDL，直接落地最小。**升级触发条件**：表数 > 10 或出现多写者/跨库迁移需求时上 SQLAlchemy 2.0 + Alembic。详见 ADR-005。

## 6. 网页后端框架

| 方案 | 实际版本 | OpenAPI 自动产出 | 静态同源托管 | WebSocket | 综合 |
|------|----------|:---:|:---:|:---:|:---:|
| **FastAPI + uvicorn** | FastAPI 0.128.0；uvicorn 0.53.x | 5（自动生成，直接产出 `openapi.yaml` 契约） | 5（`StaticFiles`） | 5 | **5** |
| Flask 3 | 3.1.x | 2（需扩展） | 5 | 3（无原生） | 3 |
| Starlette 裸用 | 0.50.x | 2（需自写校验层） | 5 | 5 | 3 |

**结论**：FastAPI 0.128.0 + uvicorn 0.53.x（上界 2026-09-22 依 ADR-006 修订为 `>=0.34,<0.54`）。开发书 §13.1 明确许可 FastAPI；自动 OpenAPI 正好满足「产出机器可读契约」要求；Pydantic 校验让 §13.2 的 `request_id`/`expected role_id` 等约束可声明式落地。详见 ADR-006。

## 7. 前端方案

| 方案 | 实际版本 | 部署端构建负担 | 时间轴编辑器可行性 | 离线/同源 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|
| **原生 ES 模块 + Web Components（无构建）** | 浏览器原生 | 5（Pi 上零 Node 工具链） | 4（单页 + Canvas/SVG 时间轴可实现，组件量可控） | 5（静态文件直接托管） | **4.5** |
| React 18 + Vite | React 18.3 / Vite 5 | 2（需 Node + 构建产物） | 5 | 4（需构建流程） | 3.5 |
| Vue 3 + Vite | Vue 3.5 / Vite 5 | 2 | 5 | 4 | 3.5 |

**结论**：MVP 用**原生 ES 模块 + Web Components，无构建步骤**。理由：部署端（Pi 4B）不引入 Node 工具链，静态文件与 API 同源（开发书 §13.1 要求），编辑器是一页 + 时间轴，规模可控。**留给设计**：目录已预留 `host/web/static/`，若后续组件数 > 25，可整体替换为 React 而不动 API 契约。**协同点**：前端框架独立于设计稿——设计交付的是视觉与交互，不绑定实现框架。详见 ADR-007。

## 8. 音频后端（跨 Windows 与树莓派的统一抽象）

| 方案 | 实际版本 | Windows 支持 | linux-aarch64 支持 | 统一 API | 综合 |
|------|----------|:---:|:---:|:---:|:---:|
| **sounddevice + soundfile** | sounddevice 0.5.6（2026-08-17，MIT）；soundfile 0.14.0（2026-06-06，BSD-3） | 5（WASAPI/MME，含 ARM64 wheel） | 5（ALSA/PulseAudio，aarch64 wheel 有） | 5（同一 PortAudio API） | **5** |
| PyAudio | 0.2.14 | 3（Windows 需预编 wheel） | 3 | 4 | 3 |
| `arecord`/`aplay` 子进程 | alsa-utils | 0（Windows 无） | 5 | 1（两套代码） | 1.5 |

**结论**：`sounddevice 0.5.6`（PortAudio 绑定，同一 API 覆盖 Windows WASAPI 与 Pi ALSA/PulseAudio）+ `soundfile 0.14.0`（读 WAV/FLAC/OGG/MP3，wheel 内自带 libsndfile，免系统依赖）。再叠加一个 **`NullAudioBackend`**，在无音频设备时以固定时长「虚拟播放/录制」，使无硬件阶段全链路可跑。**排他性**：开发书 §8.3 要求「完整应用统一使用同一个音频后端，而不是多个进程竞争声卡」——因此全局只允许一个 `AudioBackend` 实例，采集与播放互斥（半双工）。`ffmpeg` 仅用于离线格式转换（§8.2），不进运行时链路。详见 ADR-008。

## 9. 图标库选型（P0：全项目锁定一套）

| 方案 | 授权 | 图标数 | 纯 SVG/无字体依赖 | 风格一致性 | 综合 |
|------|------|:---:|:---:|:---:|:---:|
| **Lucide** | ISC（商业可用，仅需保留版权声明） | 1600+ | 5（`lucide-static` 提供裸 SVG；`lucide` 提供 vanilla `createIcons()`） | 5（严格 24px/2px 描边栅格） | **5** |
| Tabler Icons | MIT | 5900+ | 5 | 4（描边略细，量大易混用） | 4.5 |
| Material Symbols | Apache-2.0 | 3400+ | 2（官方形态以可变字体为主，需另拉 SVG 包） | 4 | 3 |

**结论**：**Lucide，唯一图标库，全项目（管理页 + 文档示意）统一使用，不得混用第二套图标，不得用 emoji 充当功能图标**。落地：`host/web/static/vendor/lucide.min.js`（vanilla，利用 `data-lucide` 属性 + `createIcons()`）随仓库 vendoring，离线可用；如需按需只引入少量，改用 `host/web/static/icons/*.svg` 内联。选 Lucide 的额外理由：同时提供 vanilla 与 React 包，未来若切 React 无需换图标库。详见 ADR-009。

## 10. 云端 AI 适配层（本阶段 Stub）

| 方案 | 实际版本 | 本阶段可运行 | 切换成本 | 契约一致性 | 综合 |
|------|----------|:---:|:---:|:---:|:---:|
| **Provider Protocol + Stub/Qwen 双实现** | Stub（自研）/ Qwen REST（`qwen3-asr-flash`、`qwen-plus`、`qwen3-tts-flash`） | 5 | 5（改一处 `AI_PROVIDER` 配置） | 5（同一 Protocol + 同一契约测试） | **5** |
| 直接用 `cloud_chat.py` 函数 | 现有示例 | 1（需要 Key 才能跑） | — | 3（无接口形状约束） | 1.5 |
| 引入百炼官方 SDK `dashscope` | dashscope SDK | 1（需 Key） | 3 | 3（SDK 版本耦合） | 2 |

**结论**：定义 `AsrProvider`/`LlmProvider`/`TtsProvider` 三个 Protocol（含 `tool_calls` 承载，供 §12.2 工具白名单），`Stub*` 从 fixture 文件给出确定性回复（支持脚本化 ASR 文本、LLM 文本与 tool_calls、TTS 固定 WAV），`Qwen*` 复用 `cloud_chat.py` 已验证的 REST 形状（HTTPS-only、错误码分类、非流式）。`AI_PROVIDER=stub|qwen` 一处切换，同一套契约测试跑两边（Qwen 侧无 Key 时 `skip`）。详见 ADR-010。

## 11. 无硬件验证方案

见独立文档 `20-mcu-simulator.md`（MCU 模拟器逐项复刻固件语义 + 三类串口来源 + 虚拟时钟 + 故障注入 + 录制回放）。对应 ADR-011。

## 12. 版本锁定清单（写入 `pyproject.toml` / `requirements.lock`）

| 包 | 锁定 | 用途 | 备注 |
|----|------|------|------|
| CPython | `>=3.13,<3.14` | 运行时 | Pi 用 Trixie；地板 3.11 兼容 Bookworm |
| pyserial | `==3.5` | 串口 | 现有 `requirements.txt` 已用 `>=3.5,<4` |
| fastapi | `>=0.128,<0.129` | Web | 编制时官方 Release Notes 最新 0.128.0 |
| uvicorn | `>=0.34,<0.54` | ASGI 服务 | 2026-09-22 依 ADR-006 修订上界（实测 0.53.0）；使用基础安装，WebSocket 协议由下项显式补齐 |
| wsproto | `>=1.2,<2` | WebSocket 协议 | 2026-10-22 依 ADR-006 补全；纯 Python，供 Uvicorn 实时事件流升级使用 |
| pydantic | `>=2.7,<3` | 校验 | FastAPI 0.126+ 起最低 2.7 |
| sounddevice | `==0.5.6` | 音频 I/O | PortAudio |
| soundfile | `>=0.13.1,<0.15` | WAV 读写 | 0.14.0 要求 Python>=3.10 |
| requests | `>=2.32,<3` | 云端 REST | 现有示例已用 |
| pytest | `>=8.3` | 测试 | 需覆盖现有 `unittest` 用例 |
| fonttools / brotli | `==4.63.0` / `==1.2.0` | 离线字体重建（可选） | 仅 `.[fonts]` 额外依赖；普通运行及测试不需要这两个包 |
| lucide | `1.x` | 图标（vendored 静态） | ISC |

> 说明：`pyproject.toml` 是依赖与工具的**唯一硬编码来源**；业务代码不得硬编码任何版本、URL、密钥、颜色。
