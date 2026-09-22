# Spec — 可换公仔 AI 语音动作玩具 · 主机程序（toy/host/）v1.0

> 生成日期：2026-09-21
> 基于：PRD v1.0（`docs/PRD-host-mvp.md`）+ 架构规格 v1.0（`docs/architecture/00-architecture-spec.md`）+ 设计规范 v1.0（`host/web/design/DESIGN.md`）
> 状态：**已生效**（三文档经用户确认后由项目总监生成，作为团队内部唯一开发依据）
> 关联契约：`docs/api/openapi.yaml`（OpenAPI 3.0.3）、`docs/architecture/30-traceability.md`、`docs/decisions/OPEN-DECISIONS.md`

---

## 1. 产品定义

- **一句话描述**：一个在普通电脑上就能完整运行的中央编排系统——状态机、音频、AI 路由、动作调度、SQLite 存储、网页管理与关键帧编排；硬件与云端以「适配器 + 确定性替身」预留接口，使全链路在无实物条件下可运行、可确定性回归。
- **目标用户**：使用者是 3–8 岁儿童（零学习成本、按键即用）；决策者是家长（内容适龄、隐私可见可删、费用有硬上限、物理安全）；维护者是用户本人（无硬件可跑、换真件只改适配器）。
- **核心问题**：现有两个孤立 CLI 无法表达「按键→录音→识别→对话→合成→说话→动作」之间的互斥与取消关系；且开发书第 9/11/13 章的安全联锁、末帧保持、记忆删除、角色匹配等关键规则目前只活在文档里、没有任何代码承载。

**诚实边界（验收文案必须遵守）**：本项目提供的是软件骨架与可复现的软件验收，**不证明**固件编译烧录、真实舵机运动、真实机械装配、真实云端账号调用。无硬件预演不是视频回放，也不是实物动作验收。

---

## 2. MVP 范围（锁定——不在此列表的功能一律不做）

### 2.1 MVP 八项（最小垂直切片：能端到端跑通「按键→AI(桩)→动作(模拟器)→记忆」）

| 优先级 | 编号 | 功能 | 验收标准摘要 | RICE |
|---|---|---|---|---|
| P0 | M1 | 中央状态机（7 状态 + 取消 + 互斥 + `session_generation`） | 非法迁移被拒并返回原因；故障锁定后必须人工确认才回空闲 | 5.40 |
| P0 | M2 | MCU 协议客户端 + In-process MCU 模拟器 | 未 ARM 不动；错角色不 ARM；300ms 无有效帧停 PWM；末帧保持 ≥150ms 再 STOP | 5.40 |
| P0 | M3 | 动作时间线 + 限位/速度校验 + 离线预演 | 1399/1601/NaN/Inf 全拒；峰值速度 ≤300μs/s；首末帧中位 | 5.33 |
| P0 | M6 | AI 路由 + 后端适配器（真云适配器 + 确定性桩） | 无 Key 自动落桩、零网络出口；工具调用经本地白名单二次校验 | 4.80 |
| P0 | M14 | 儿童模式内容边界 | 输出审核在合成/播放**之前**完成；固定拒绝模板；夜间不主动发声 | 4.80 |
| P0 | M4 | SQLite 长期记忆（查看/删除/清空/关闭） | 重开连接可读；删除后新会话不再注入（含历史与摘要缓存） | 4.67 |
| P0 | M5 | 角色注册与切换（role_id 1/2/3 + `session_generation`） | 换角色后旧 generation 的迟到回复被丢弃；旧 role_id 播放请求被拒 | 4.67 |
| P0 | M10 | 音频抽象层（真后端 + 文件后端 + Null） | 无设备可跑全链路；半双工互斥（说话时不监听） | 3.50 |

### 2.2 Backlog（**接口须预留**，本阶段不实现）

| 优先级 | 编号 | 功能 | 预留落点 | RICE |
|---|---|---|---|---|
| P1 | M7 | 工具白名单本地执行 | `host/ai/tools.py` | 3.20 |
| P1 | M13 | 三按键行为状态表 | `host/core/state_machine.py`（§7.1.1 矩阵已定稿）+ `host/audio/volume.py` | 2.50 |
| P1 | M9 | 本地内容库 + 本地表演（统一单调时钟） | `host/audio/catalog.py` + `host/motion/timeline.py` | 2.40 |
| P1 | M11 | 网页管理 + 关键帧编排 | `host/web/routers/*` + `host/web/static/editor.*` | 1.60 |
| P2 | M8 | 提醒任务（时区 + 幂等） | `host/tools/reminders.py` + `reminders_repo.py` | 1.33 |
| P2 | M12 | 资源包导入/导出（安全检查） | `host/roles/rolepack.py` | 1.33 |

> **切分说明**：通用 PM 规则是 MVP 只留 RICE 最高的 1–3 项，本交付不适用——交付物是编排系统而非单点功能，只留 1–3 项的主机跑不起来、无法演示闭环。此项偏离已由项目总监裁决认可。

### 2.3 三个适配器（结构性前提，MVP 全部用确定性替身）

| 适配器 | 真实现（预留、默认不启用） | MVP 替身 | 能证明 / 不能证明 |
|---|---|---|---|
| `MotionTransport` | 真串口连 ESP32 | In-process MCU 模拟器（复刻 `motion_controller.ino`） | 能证明主机侧协议/时序/联锁；**不能**证明固件与机械 |
| `ChatBackend` | 真 Qwen ASR/对话/TTS | 确定性桩后端（夹具驱动，零网络出口） | 能证明路由/白名单/审核挂钩；**不能**证明识别生成质量 |
| `AudioIo` | 真声卡 | 文件后端 + Null 后端 | 能证明编排与半双工互斥；**不能**证明声卡延迟与回声 |

> **硬要求**：未设置 `DASHSCOPE_API_KEY` 时，`ChatBackend` 必须自动落到桩后端并可运行。

---

## 3. 明确不做（Out-of-Scope — 锁定）

| 不做的功能 | 原因 | 何时考虑 |
|---|---|---|
| 真实舵机运动 / ESP32 编译烧录 / 急停断电实测 | 物料未采购；开发书第 20 章要求实物证据，不能造假 | 样机 A 到货后 |
| 真实 ASR / LLM / TTS 云端调用 | 本阶段明确不接百炼 | 用户开通 Key 后 |
| 真实声卡录音播放、AEC 回声消除 | 无音频硬件；真正「边说边打断」需 AEC 与参考通路 | 性能阶段 |
| 流式 ASR / 对话 / TTS | 开发书 10.1 明确「不要在第一步就实现流式」 | 链路稳定后 |
| OTA 固件升级 / 双分区 / 签名回滚 | 无固件可升级 | 阶段 C |
| 自定义 PCB / 电池 / 机械 CAD | 前置条件不具备 | 整机功耗实测后 |
| 公共技能市场 / 账号 / 计费 / 审核后台 | 属独立服务工程 | 不排期 |
| 声纹 / 语音身份识别 | 开发书 11.2 明确「不能仅靠声纹推断儿童身份」 | 不做 |
| Scratch 式积木编辑器 | MVP 先做关键帧时间线 | 不排期 |
| 唇形同步 / 灯效联动 | 依赖实物与音画实测 | 不做 |
| 开机自启 systemd 服务 | 无树莓派部署环境 | 硬件到位后 |
| 前端构建链（TS / npm / MSW Mock） | 与「无 Node 构建」选型直接冲突，产出即死代码 | 组件数 > 25 时再评估 |

---

## 4. 技术架构（锁定 — 含版本锚定）

| 层 | 技术 | 版本锚定 | 锁定原因 |
|---|---|---|---|
| 主机语言/运行时 | Python + venv | CPython `>=3.13,<3.14`（地板 3.11） | 现有 `motion.py`/`cloud_chat.py` 已是 Python；Pi OS Trixie 自带 3.13，开发与部署同版本 |
| 状态机 | 自研显式状态表 | 无第三方依赖 | 7 状态约 14 迁移，表即规格可穷举测试；>12 状态再迁 `python-statemachine 3.2.1` |
| 并发 | 领域线程 + 线程安全队列 + 单线程确定性核心 | 标准库 `threading`/`queue` | 串口线程与网络线程物理隔离；网络不得阻塞串口与按键 |
| 串口抽象 | `MotionLink` Protocol + Serial/Sim/Replay 三实现 | `pyserial==3.5` | 模拟器以 TCP 暴露，`SerialLink` 用原生 `socket://` 连接，同一份代码对接真机与模拟器 |
| 数据库 | 标准库 `sqlite3` 门面 + `user_version` 迁移 | 标准库（无依赖） | 开发书 §11 本就是裸 DDL，上 ORM 属过剩 |
| 网页后端 | FastAPI + uvicorn（同源托管静态页） | `fastapi>=0.128,<0.129` / `uvicorn>=0.34,<0.54` | 自动产出 OpenAPI 契约；Pydantic 声明式落地 `request_id`/`expected role_id` |
| 前端 | 原生 ES 模块 + Web Components，**无 Node 构建** | 浏览器原生（无 npm） | Pi 上零 Node 工具链；静态文件与 API 同源 |
| 音频 | sounddevice + soundfile + Null 后端可切换 | `sounddevice==0.5.6` / `soundfile>=0.13.1,<0.15` | 同一 PortAudio API 覆盖 Windows WASAPI 与 Pi ALSA；全局单实例、半双工 |
| 图标库（P0 锁定） | **Lucide**，全项目唯一 | `lucide 1.x`（ISC） | 24px/2px 描边栅格一致；同时提供 vanilla 与 React 包 |
| 云端 AI | Provider Protocol + Stub/Qwen 双实现 | Stub（本阶段）/ Qwen REST | `AI_PROVIDER=stub\|qwen` 一处切换，同一契约测试跑两边 |
| 表单校验 | Pydantic | `pydantic>=2.7,<3` | FastAPI 0.126+ 最低要求 |
| 测试 | pytest | `>=8.3` | 需覆盖现有 `unittest` 用例 |

**架构铁律**：依赖只能向下；`web/` 路由禁止直读数据库；`host/core/` 禁止 import HTTP 对象；仓储禁止含业务规则；`host/main.py` 只装配、**<100 行**；单文件 **≤300 行**。

---

## 5. API 端点清单（锁定——开发以 `docs/api/openapi.yaml` 为唯一依据）

Base URL：`http://127.0.0.1:8000/api/v1`（默认仅绑回环；开放局域网需登录 + 防 CSRF）

| Method | Path | 功能 | 请求体 | 响应 | 错误码 |
|---|---|---|---|---|---|
| GET | `/status` | 当前角色、网络、动作、故障 | — | `Status` | 200 |
| GET | `/events` | 实时状态与故障事件流（WebSocket） | — | WS 流 | 101 / 401 |
| GET | `/features` | 当前功能开关状态 | — | — | 200 |
| GET | `/roles` | 可用角色及配置版本 | — | `RoleSummary[]` | 200 |
| PATCH | `/roles/active` | 切换当前角色 | — | `Envelope` | 200 / 409 |
| POST | `/actions/validate` | **只校验，不移动硬件** | `ActionValidateRequest` | `ActionValidateResult` | 200 |
| POST | `/actions/play` | 当前角色匹配且安全检查成功才播放 | `ActionPlayRequest` | `Envelope` | 200 / 409 |
| POST | `/actions/stop` | 立即取消队列并通知 MCU | — | `Envelope` | 200 |
| POST | `/content/import` | 限大小、路径与签名校验后导入 | multipart | `Envelope` | 201 / 400 |
| GET | `/memories` | 查看当前档案记忆 | — | `Memory[]` | 200 |
| DELETE | `/memories/{id}` | 删除单条记忆 | — | `Envelope` | 200 |
| GET | `/reminders` | 查询提醒 | — | `Reminder[]` | 200 |
| POST | `/reminders` | 创建持久提醒 | `ReminderCreateRequest` | `Envelope` | 201 / 422 |
| DELETE | `/reminders/{id}` | 取消提醒 | — | `Envelope` | 200 |
| GET | `/settings` | 音量、语言、配网设置（密码不回显） | — | `Settings` | 200 |
| PATCH | `/settings` | 修改设置 | `SettingsPatch` | `Envelope` | 200 |

**契约约束（全部强制）**：
1. 统一响应信封 `{code, data, message}`
2. **每个写操作必须带 `request_id`**
3. 播放请求必须附**预期 `role_id`** 与动作版本；第二个播放请求默认拒绝或显式替换，**绝不静默叠加两个时间轴**（返回 409）
4. `/status` 及任何端点**不得返回密钥**

Schema 共 14 个：`Envelope`、`Status`、`Fault`、`RoleSummary`、`ActionDoc`、`ActionValidateRequest`、`ActionValidateResult`、`ActionPlayRequest`、`ActionPlayResult`、`Memory`、`Reminder`、`ReminderCreateRequest`、`Settings`、`SettingsPatch`。

---

## 6. 数据库表清单（锁定）

SQLite，`PRAGMA foreign_keys=ON`，全部查询参数绑定，`user_version` 驱动前向迁移（`host/storage/migrations/NNNN_*.sql`）。

| 表名 | 核心字段 | 索引 | 关联 |
|---|---|---|---|
| `profiles` | `id` PK、`display_name` NOT NULL、`created_at` NOT NULL | PK | 被 `memories` / `reminders` 外键引用 |
| `memories` | `id` PK、`profile_id` FK NOT NULL、`kind` NOT NULL、`value` NOT NULL、`confirmed` NOT NULL DEFAULT 0、`created_at` NOT NULL、`expires_at` | `memories_by_profile(profile_id, kind)` | → `profiles.id` |
| `reminders` | `id` PK、`profile_id` FK、`text`、`due_at_utc`、`timezone`、`repeat_rule`、`status`、`last_fired_at`、`idempotency_key` UNIQUE | `reminders_by_due(due_at_utc, status)`、`idempotency_key` UNIQUE | → `profiles.id` |
| `settings` | `key` PK、`value`、`updated_at` | PK | 单行键值，含音量/语言/儿童模式开关 |

**数据纪律**：原始录音处理后默认删除，**不把音频路径永久写入记忆**；长期摘要属不可信数据，注入时必须数据化封装并附明确非指令声明，**不得当作系统指令**。

---

## 7. 页面清单（锁定）

| 页面 | 路由 | 核心组件 | 对应 API | 主题 |
|---|---|---|---|---|
| 设备状态面板 | `/` | 顶栏 Connection State Chip（常驻）、四轴限位横条（绿/琥珀/红 + 1500μs 中位刻度）、故障区 | `/status`、`/events`、`/features` | 浅色 |
| 动作编排器 | `/editor` | 三栏工作台：左 260px 四轴轨道列表 / 中时间轴（刻度尺 + 可拖播放指针 + 菱形关键帧 + 音频波形轨 + 播放控制条）/ 右 300px 属性与限位校验面板 | `/actions/validate`、`/actions/play`、`/actions/stop`、`/roles`、`/roles/active` | **深色（默认）** |
| 记忆管理 | `/memories` | 列表（摘要/类型/来源时间/状态/操作）+ 行内删除确认 + 清空弹窗 | `/memories`、`/memories/{id}` | 浅色 |
| 提醒管理 | `/reminders` | 列表（时间/内容/角色音色/状态/下次触发）+ 新建表单（≤4 字段） | `/reminders`、`/reminders/{id}` | 浅色 |
| 设置 | `/settings` | 音量语言 / 网络配网 / 儿童模式 / 内容审核 / 高级危险区 | `/settings`、`/content/import` | 浅色 |

**界面硬约束**：
- 数值区在无数据时显示 `—` 并加斜纹底，明确「无数据 ≠ 0」
- 设备实时值严格标注为「**目标位置**」，**禁止**显示为「实际角度」（PWM 舵机无位置反馈）
- 离线在本阶段是**预期常态**而非错误：界面照常可编辑，文案中性并给「重试 / 连接指引」两条出路
- 危险区永远置底独立分组；恢复出厂需输入 `RESET` + 5 秒冷却

---

## 8. 设计 Token（锁定）

**唯一色值落点**：`host/web/design/design-tokens.css`（`design-tokens.json` 为机器可读镜像，须与 CSS 完全等价）。除该文件与 SVG 内 `#fff`/`#000` 外，**任何文件不得出现字面色值**。

| 项 | 值 |
|---|---|
| **主色** | 浅色 `--accent: #0F766E`（石油青）／深色 `#14B8A6` |
| **主题** | 浅色 = 管理页默认（家长阅读）；深色 = 编排器默认（长时间精调），`<html data-theme="dark">` 切换 |
| **字体** | display `Lexend` / body `Figtree` / mono `JetBrains Mono` / 中文回退 `Noto Sans SC`；**本地 @font-face 自托管**，等宽优先回退链保证 µs 数值列对齐 |
| **图标库** | **Lucide 1.x（ISC）**，全项目唯一；尺寸阶梯 16 / 20 / 24px，16px 时描边降为 1.75 |
| **间距 / 圆角** | 4px 网格（`--space-1..16`）／`--radius-sm 6px` / `-md 10px` / `-lg 14px` / `-pill` |
| **动效** | 120 / 180 / 260ms，缓动 `cubic-bezier(0.2, 0, 0, 1)` |
| **领域色** | 限位 `--limit-normal/near/at`（绿/琥珀/红）+ `--limit-neutral-mark`；连接态 `--conn-offline/online/active/fault`；四轴 `--track-1..4`；播放指针 `--playhead` |
| **对标品牌** | 「可校准的精密仪器」——Teenage Engineering 的克制标签语言 + Bottango 的关键帧范式 + Helios GCS 的交规式限位 |

**门禁**：静态资源中 `#[0-9a-fA-F]{3,8}`、`rgb(`、`hsl(` 命中即不合格（`#fff`/`#000` 与 `design-tokens.css` 除外）；`gradient` 含紫粉系即不合格。

---

## 9. 验收标准（锁定——QA 以此为准，EARS 格式）

> 全部为**软件可复现验收**（无硬件、无云端），对应开发书第 20 章「样机源代码测试」一栏，**不替代**实物证据。

| 编号 | 功能 | EARS 验收标准 | 优先级 |
|---|---|---|---|
| AC-01 | 状态机互斥 | While 状态机处于 `RECORDING`，When 收到表演键事件，系统**必须**取消录音、删除临时录音并离开录音态 | P0 |
| AC-02 | 故障锁定 | While 状态机处于 `FAULT_LOCKED`，If 未收到人工确认，系统**必须**永不自动回到 `IDLE` 或重新解锁电机 | P0 |
| AC-03 | 抢占式停机 | While 处于任意运动状态，When 触发拔出 / 急停 / 串口超时，系统**必须**进入停机并锁定 | P0 |
| AC-04 | 未解锁防护 | Given 模拟器未 `ARM`，When 主机发送 `FRAME`，模拟器**必须**返回 `ERR FRAME` 且不产生 PWM 目标 | P0 |
| AC-05 | 角色匹配 | Given 期望角色 1，When 模拟器识别到角色 2，主机**必须**不发送 `ARM` | P0 |
| AC-06 | 看门狗 | Given 主机持续发 `FRAME`，When 模拟器 300ms 内无有效帧，系统**必须**停机并上报 `FAULT INTERLOCK_OR_TIMEOUT` | P0 |
| AC-07 | 末帧确认 | Given 末帧已到达，When 收不到 `SETTLED`，主机**必须**在 2 秒内报错停机，且 `finally` 路径**必须**发出 `STOP` | P0 |
| AC-08 | 末帧保持 | Given 末帧目标已确认，主机**必须**保持 ≥150ms 再发 `STOP` | P0 |
| AC-09 | 动作数值校验 | Given 脉宽 1399 / 1601 / NaN / Inf / 布尔 / 字符串，When 校验，系统**必须**全部抛 `ValueError` | P0 |
| AC-10 | 速度与时间校验 | Given 时间非严格递增，或峰值速度 >300μs/s，When 校验，系统**必须**拒绝 | P0 |
| AC-11 | 离线预演 | Given 合法动作，When 无硬件预演，系统**必须**保持四轴全程在 1400–1600μs 且首末帧为 1500 | P0 |
| AC-12 | 记忆持久化 | Given 已确认的「喜欢恐龙」已写入 profile，When 关闭并重开数据库连接，系统**必须**可读回该记忆 | P0 |
| AC-13 | 删除生效 | Given 某条记忆被删除，When 新建会话构建上下文，该事实**必须**不出现在注入上下文中（含短期历史与摘要缓存） | P0 |
| AC-14 | 记忆开关 | Given 记忆开关已关闭，When 用户说「记住……」，系统**必须**不写入 | P0 |
| AC-15 | 换角色竞态 | Given 角色 1 正在等待 AI 回复，When 切换到角色 2，角色 1 的迟到回复**必须**不播放，且 `session_generation` **必须**已自增 | P0 |
| AC-16 | 旧 role_id 拒绝 | Given 网页请求携带旧 `role_id`，When 当前角色已变更，播放请求**必须**被拒绝 | P0 |
| AC-17 | 零网络出口 | Given 未设置 `DASHSCOPE_API_KEY`，When 启动与对话，系统**必须**使用桩后端运行且**零网络请求**，不报致命错误 | P0 |
| AC-18 | 工具参数校验 | Given 桩后端返回「调用 get_weather」，When 参数非法，系统**必须**本地拒绝且**不得编造天气** | P0 |
| AC-19 | 审核前置 | Given 一条将被播放的模型输出，When 输出审核判定不合格，系统**必须**在**合成 / 播放之前**拦截，绝不「先说出来再撤回」 | P0 |
| AC-20 | 固定拒绝模板 | Given 涉危险现实操作的问题，系统**必须**引导找成年人；Given 索要住址 / 学校 / 密码，系统**必须**固定拒绝 | P0 |
| AC-21 | 半双工互斥 | While 正在播放，When 收到录音请求，系统**必须**先停止播放再开始录音 | P0 |
| AC-22 | 非阻塞 | While 网络线程注入 10 秒假请求，系统**必须**在 <100ms 内仍能收发一行串口并处理按键事件 | P0 |
| AC-23 | 编排校验接口 | Given 非法动作包，When 调 `POST /api/v1/actions/validate`，系统**必须**返回拒绝且**不触发任何硬件动作** | P0 |
| AC-24 | 不叠加时间轴 | Given 已有播放中的时间轴，When 收到第二个播放请求，系统**必须**默认拒绝或显式替换，**不得静默叠加** | P0 |
| AC-25 | 资源包安全 | Given 含 `../`、绝对路径、符号链接、超量文件或高压缩比的 zip，When 导入，系统**必须**全部拒绝，失败**必须**保留旧版本 | P1 |
| AC-26 | 三按键表 | When 注入 `BUTTON n` 事件，系统**必须**逐格符合开发书 5.2 行为表（含「未装公仔不启用电机」） | P1 |

---

## 10. 边界与约束

| 类别 | 约束 |
|---|---|
| 边界值 | 脉宽 1400–1600μs（**固件硬限位**）；动作时长 ≤60s；帧数 2–1000；录音单次 ≤15s；协议行 ≤99 字符 |
| 限位真源 | 主机侧限位真源 = 角色配置的 `min_us`/`max_us`（开发书 §9.3）；固件的 1400–1600μs 仅作台架保守值保留、**不改动**，两边各自成立 |
| 兼容性 | Windows 10/11 + Python 3.11+（主机可在 PC 跑）；浏览器 Chrome/Edge/Firefox/Safari 最新 2 版 |
| 性能 | 管理页首屏 <3s；状态切换 p95 <500ms（不含云端等待）；帧调度按单调时钟采样，**禁止逐帧 sleep 累加** |
| 安全 | 云 API 强制 HTTPS + `allow_redirects=False`；密钥仅环境变量或仅服务账户可读文件；SQLite 开外键 + 参数绑定；网页默认只绑 `127.0.0.1`；导入包防路径穿越 / 符号链接 / 压缩炸弹 |
| 离线降级 | 断网仍可用：本地动作、本地内容、已确认记忆读取、时钟有效时的提醒；不可用：AI 对话（必须明确提示，不得静默失败） |
| 时间不可靠 | 冷启动无网络无法确认时间时，**必须**暂停绝对时间提醒并提示校时，不静默错发 |
| 可观测 | 结构化日志（脱敏：不记密钥、不记儿童原始输入全文）；记录五个时间戳（录音结束 / 识别完成 / 首字 / 合成完成 / 开始播放） |
| 埋点 | 事件命名 `{对象}_{动作}`；**只落本地**，不接第三方 SDK；不上报原始录音、不采集 IP、不存儿童输入全文 |
| 儿童模式 | 双层审核且输出审核在播放前完成；工具白名单禁任意 URL / 命令 / 脉宽；记忆默认最小化、访客不持久、删后清缓存；夜间不主动发声；静音真停采集；录音有可见指示 |

---

## 11. 内嵌已知坑（防重蹈覆辙）

> 来源：`AI角色玩具开发书/验证记录.md` 与固件/示例代码的既有语义。`pitfalls.jsonl` 尚未建立，本阶段先以「已确认的固件行为」形式内嵌。

| 坑 | 技术栈指纹 | 根因 | 修法 |
|---|---|---|---|
| 重复 `ARM` 会被拒 | `motion_controller.ino` | 固件对已 `armed` 状态返回 `ERR ALREADY_ARMED` | 主机每轮播放**必须**先 `STOP` 再 `STATUS` 校验再 `ARM` |
| `previousSeq` 在 `stopMotion()` 中**不重置** | 同上 | 这是**有意的重放防护**，非缺陷 | 模拟器**必须**逐字复刻；主机每轮重新 `ARM` 时因 `haveSeq=false` 自然复位，不依赖该字段清零 |
| `ARM` 尾随多余 token 会落到 `ERR COMMAND` | 同上 | 固件用 `sscanf("ARM %d %c") == 1` 解析 | 主机严格只发 `ARM <int>`，不加任何尾随字符 |
| `FRAME` 序号必须严格递增 | 同上 | `seq > previousSeq` 才有效，否则 `ERR FRAME` 且停 PWM | 序号由主机单调维护，回绕语义见 OD-2 |
| 单纯查询 `STATUS` **不能**维持运动 | 同上 | 只有有效 `FRAME` 刷新 300ms 看门狗 | 长动作必须持续发帧，不能靠轮询状态保活 |
| 无硬件预演不等于实物验收 | 项目级 | 预演只打印脉宽轨迹，不产生真实运动 | 验收文案必须区分「软件可复现」与「实物证据」两类 |
| 退格/换行处理 | 固件串口 | 固件跳过 `\r`、以 `\n` 断行、行缓冲 100 字节、每循环最多处理 64 字节 | 主机侧不得依赖超出 99 字符的行，也不得假设一次性写完整行 |

---

## 12. 端到端验证步骤（Spec 锁定的最后一项）

```bash
# 前置：cd toy && python -m venv .venv && .venv/Scripts/python -m pip install -e .

# 1) 单元与契约测试
pytest tests/unit tests/contract -q
# 断言：全绿；含动作校验与旧 motion.py 的一致性比对

# 2) 模拟器故障路径（不止 happy path）
pytest tests/integration -q
# 断言：未到位/急停未健康/角色不符/稳定期<200ms → ERR INTERLOCK
#      重复 ARM → ERR ALREADY_ARMED；越界/序号非递增/未解锁 → ERR FRAME 且 PWM 停
#      运动中停发帧 >300ms → FAULT INTERLOCK_OR_TIMEOUT
#      运动中拔线/急停/换角色 → FAULT；超长行 → ERR LENGTH；未知命令 → ERR COMMAND
#      启动 pwmReady=false → FAULT PWM_INIT

# 3) 非阻塞证明
pytest tests/integration -q -k network_does_not_block_serial

# 4) 无硬件全链路
python -m host.main --simulate
curl http://127.0.0.1:8000/api/v1/status
# 断言：返回当前状态与故障字段
curl -X POST http://127.0.0.1:8000/api/v1/actions/play \
     -H 'Content-Type: application/json' \
     -d '{"request_id":"r1","expected_role_id":1,"action_id":"wave"}'
# 断言：200；模拟器收到 ARM/FRAME/STOP；动作完成回 IDLE
# 断言：重复发第二个播放请求 → 409，不叠加

# 5) 黄金回放比对
python -m sim --replay tests/fixtures/sessions/*.jsonl --diff
# 断言：产出的 MCU→主机 行与录制逐行一致

# 6) 门禁
pytest -q && ruff check .
# 目录门禁：host 下无 .py 超过 300 行、main.py 少于 100 行
# 颜色门禁：静态资源无字面色值（#fff/#000 与 design-tokens.css 除外）
# Emoji 门禁：全树零 emoji
```

> **判定**：上述 1–6 全过 = 本 Spec「无硬件档位」交付完成。**这不等于**开发书第 20 章的整机验收；第 20 章的实物项按架构规格 §10.1 的 C1–C13 保留为待验证。

---

## 13. 变更记录

| 日期 | 变更内容 | 原因 | 影响范围 |
|---|---|---|---|
| 2026-09-21 | Spec v1.0 生成 | 三文档经用户确认，按 SOP 进入 Phase 1.5 | 全部 |
| 2026-09-22 | 修正 §12 判定段交叉引用：架构 §10.1 实物项枚举 `C1–C12` → `C1–C13` | 架构 §10.1 新增 C13（桩级输出审核的误拦边界）后本处编号未同步；由运维卜宕机交叉核对发现 | 仅 §12 一处词面，无功能/范围变更 |
| 2026-09-22 | 同步 §4 版本锚定：uvicorn 上界 `>=0.34,<0.53` → `>=0.34,<0.54` | 依 ADR-006（2026-09-22，Lead 裁决）修订；SPEC 原值落后于 pyproject（依赖唯一真源，已为 `<0.54`） | 仅 §4 一处版本号，无功能/范围变更 |
