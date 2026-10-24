# PRD → 落地文件 → 用户可见行为 → 验收方式（追溯表）

> 状态：Accepted（2026-09-21）· 负责人：高见远（首席架构师）
> 作用：把 `docs/PRD-host-mvp.md` 的功能编号 **M1–M14** 逐条绑到**可施工的代码落点**、**用户能看到的行为**、**可复现的验收方式**。这是 Spec 的必需输入，也是「无产物不设席位」的检查点。
> 依据：`00-architecture-spec.md`（§4 目录 / §5 兼容契约 / §7 状态机协议 / §10.4 已定死约束 / §11 E2E）、`10-tech-selection-matrix.md`、`20-mcu-simulator.md`、`../api/openapi.yaml`。
> 约定：路径均相对 `toy/`。`【预留】` = 本阶段只交付**接口骨架 + Spec 条目**，实装进 Backlog（不占 MVP 工期）。

## 0. 读表须知

- **MVP 垂直切片**（PRD §6）：M1 + M2 + M3 + M6 + M14 + M4 + M5 + M10 —— 对应闭环「按键 → AI(桩) → 动作(模拟器) → 记忆」。
- **Backlog P1**：M7 / M13 / M9 / M11；**Backlog P2**：M8 / M12。
- **列含义**：
  - *用户可见行为* = 使用者（儿童）/决策者（家长）/维护者**能观察到**的结果，不是内部实现细节。
  - *验收方式* = 无硬件、无云端条件下的**确定性、可复现**证据（pytest 用例名 / E2E 步骤 / 合同测试）。对应开发书 §20「样机源代码测试」一栏，**不替代实物证据**。
- 验收证据统一约束：核心走**注入 `VirtualClock` + 单线程确定性核心**（`00-architecture-spec.md` §9.3），失败必须可复现。

## 1. MVP 切片（8 项，本阶段实装）

| M | PRD 功能 | 落地文件路径（`toy/` 下） | 用户可见行为 | 验收方式 |
|---|----------|---------------------------|--------------|----------|
| **M1** | 中央状态机（空闲/录音/思考/说话/表演/故障锁定）与合法转换 | `host/core/state_machine.py`（§7.1 显式迁移表）、`host/core/engine.py`（消费事件→驱动 SM）、`host/core/events.py`、`host/core/bus.py`、`host/core/clock.py` | 按对话键进录音、说话时再按可打断取消、录音时**停电机**、故障后**必须人工确认**才回空闲（不自动复位） | `tests/unit/test_state_machine.py`（`VirtualClock` 穷举 §7.1 全部迁移；非法迁移返回 `(False, reason)` 不抛未捕获异常）；PRD **A1**；E2E §11 步骤 4 |
| **M2** | MCU 协议客户端 + In-process MCU 模拟器 | `host/motion/client.py`（STATUS/ARM/FRAME/SETTLED/STOP 状态机）、`host/motion/link.py`（`MotionLink` Protocol）、`host/motion/serial_link.py`、`host/motion/sim_link.py`、`host/motion/replay_link.py`；模拟器 `sim/controller.py`、`sim/wire.py`、`sim/devices.py`、`sim/tcp_bridge.py` | 播放动作有**到位确认**；未装公仔/急停未健康时**不动电机**；串口超时/拔线立刻进故障锁定 | `tests/contract/test_protocol_parity.py`（同一套协议测试同时跑真实现与模拟器）；`tests/integration/`（§11 步骤 2 的**故障路径**：未 ARM 发 FRAME→`ERR FRAME`、错角色不 ARM、300ms 无有效帧→`FAULT`、末帧保持 ≥150ms 再 STOP、缺 SETTLED 2s 停机）；PRD **A2** |
| **M3** | 动作时间线 + 限位/速度校验 + 离线预演 | `host/motion/validation.py`（单一真源，对齐 `software/motion.py`）、`host/motion/interpolation.py`（`s(u)=3u²−2u³`）、`host/motion/timeline.py`（按经过时间采样） | 非法动作在**播放前**被拒并提示原因；维护者可用 `--dry-run` 看四轴脉宽轨迹 | `tests/unit/test_validation.py`、`tests/contract/test_motion_parity.py`（fuzz 语料比对旧 `motion.py`，不可达则 `skip`）；PRD **A3**；E2E §11 步骤 1 |
| **M6** | AI 路由 + 后端适配器（真云适配器 + 确定性桩） | `host/ai/router.py`（ASR→输入审核→LLM→工具→输出审核→TTS）、`host/ai/providers.py`（Protocol）、`host/ai/stub_provider.py`、`host/ai/qwen_provider.py` | **无 key 也能对话**（桩后端）；不联网即可跑通全闭环；联网失败时有本地提示不崩溃 | `tests/contract/test_provider_contract.py`（断言 Stub **零网络出口**、错误分类 401/403/404/429）；PRD **A6**；E2E §11 步骤 4 |
| **M14** | 儿童模式内容边界（输入审核 + 输出审核 + 家长设置） | `host/ai/safety.py`（双层审核 + 固定模板）、`host/ai/router.py`（审核**出声前**完成）、`host/web/routers/settings.py`、`host/tools/`（白名单执行） | 不当输出在**出声前**被拦（绝不"先说出来再撤回"）；夜间不主动发声；静音是**真停采集**；索要住址/学校/密码固定拒绝 | `tests/unit/test_safety.py`（边界夹具集：断言审核在合成/播放之前；断言固定拒绝模板）；PRD **A7**；§0 第 5 条（记忆数据化封装）由 `host/ai/router.py`+`safety.py` 落点验证 |
| **M4** | SQLite 长期记忆（profiles/memories：查看/删除/清空/关闭） | `host/storage/database.py`（sqlite3 门面：外键/参数绑定/`user_version`）、`host/storage/migrations/0001_init.sql`、`host/storage/profiles_repo.py`、`host/storage/memories_repo.py`、`host/web/routers/memories.py` | 家长可在记忆页**查看/删除单条/清空/关闭**；删除后新会话**不再"记得"**（含短期历史与摘要缓存） | `tests/unit/test_storage.py`（写入→关连接→重开可读；删除后新建会话不再注入；断言外键开启、全部查询参数绑定）；PRD **A4**；E2E §11 步骤 4（持久化） |
| **M5** | 角色注册与切换（role_id 1/2/3、配置、`session_generation`） | `host/roles/registry.py`、`host/roles/schema.py`（Pydantic）、`host/web/routers/roles.py`；`session_generation` 落 `host/core/state_machine.py`+`engine.py` | 换公仔后**旧角色的迟到回复不再播放**；网页播放请求须 `role_id` 与当前一致否则被拒 | `tests/unit/test_roles.py`（切角色 `session_generation` 自增；旧 generation 迟到回复被丢弃；播放前 role_id 校验）；PRD **A5** |
| **M10** | 音频抽象层（真后端 + 文件后端） | `host/audio/backend.py`（Protocol）、`host/audio/null_backend.py`（无设备默认）、`host/audio/sd_backend.py`、`host/audio/recorder.py`、`host/audio/player.py` | 按对话键有录音/播放反馈（无设备时以定长虚拟录放呈现）；**说话时不监听**（半双工互斥） | `tests/unit/test_audio.py`（文件后端跑 录音夹具→识别→合成→播放 全链路；断言半双工互斥）；E2E §11 步骤 4；PRD §5.2 M10 |

2026-10-22 补充 M2 的运行中取消回归：`host/adapters/motion_execution.py` 负责在途执行与结果回投，`execution.py` 保留队列调度；停止、替换、关停及安全锁定均使旧运动取消，旧结果不算完成。证据为 `tests/unit/test_motion_cancellation.py` 与真实 pyserial/TCP 的 `tests/integration/test_motion_interrupt.py`；TCP 空闲采样与看门狗见 `tests/integration/test_tcp_idle.py`。这些软件证据不替代实物 C3/C5。

2026-10-24 补充 M11 状态页目标链路：`host/motion/client.py` 发布已写入 FRAME，`host/adapters/status.py` 提供有归属和有效期的副本快照，HTTP/WS 共同传输，`pages/status-targets.js` 向四轴组件注入目标并按角色限幅展示。停止、完成、故障、换角色、配置变化、过期及断线均不得残留目标；旧 HTTP 回包和旧播放回调不得覆盖新状态。回归见 `tests/unit/test_motion_progress.py`、`tests/integration/test_motion_targets.py`、`tests/frontend/verify_status_targets.mjs`；工程外 Chrome/CDP 已检查桌面、窄屏、过期和断线渲染。这些数值为指令目标，不是位置反馈，也不代表 C1/C2/C6 验收。

## 2. Backlog P1（4 项，接口骨架 + Spec 条目预留）

| M | PRD 功能 | 落地文件路径（**预留位置**） | 用户可见行为（目标） | 验收方式（预留） |
|---|----------|------------------------------|----------------------|------------------|
| **M13** | 三按键行为状态表 | **Spec 已定稿**：`00-architecture-spec.md` §7.1 迁移表 + **§7.1.1 三按键×状态矩阵**；**代码落点预留**：`host/core/state_machine.py`（`DIALOG_PRESS`/`PERFORM_PRESS`/`VOLUME_PRESS`）、`host/core/engine.py`（音量意图路由）、`host/audio/volume.py`（三档 low/mid/high，真源） | 对话/表演键切换状态；**音量键切三档且不改变状态**（录音态显式 no-op 以免污染识别） | 向状态机注入 `BUTTON n` 事件，**逐格断言** §7.1.1 矩阵（含"未装公仔不启用电机"）；PRD §5.2 M13 原文 |
| **M7** | 工具白名单本地执行（get_weather/create_reminder/play_content/play_action/cancel_reminder） | `host/ai/tools.py`（白名单注册与参数校验）、`host/tools/weather.py`、`host/tools/reminders.py`、`host/tools/content.py` | 说"提醒我…"能建提醒；查天气失败时**明确说查不到**而非编造；未知/越界工具被拒 | 桩后端产出工具调用 → 断言参数越界/未知工具被拒；断言"仅数据库事务成功才回'已设置'"；`get_weather` 无 key 明确失败；PRD §5.2 M7、**A6** |
| **M9** | 本地内容库 + 本地表演（音频+动作统一单调时钟） | `host/audio/catalog.py`（内容库 + 响度/峰值检查）、`host/tools/content.py`（`play_content`/`play_action`）、`host/motion/timeline.py`（统一单调时钟） | **断网也能**听本地故事/看本地表演；音频与动作同步 | 音频文件 + 动作时间线在**同一单调时钟**下采样；断言断网（桩后端抛错）仍可播放；断言"按经过时间采样"而非"每帧 sleep 累加"；PRD §5.2 M9 |
| **M11** | 网页管理 + 可视化关键帧编排（validate/play/stop/import/export） | `host/web/routers/actions.py`、`host/web/routers/content.py`、`host/web/static/editor.html`、`editor.js`、`timeline.js`、`host/roles/rolepack.py`；契约见 `../api/openapi.yaml` | 网页拖关键帧编动作、**预演**（不触硬件）、停止立即取消、导入导出角色包 | FastAPI `TestClient`：校验接口不触硬件；播放接口要求 `role_id` 匹配；`stop` 立即取消队列；连续两个播放请求按规则**拒绝/替换而非静默叠加**；PRD **A8** |

2026-10-23 补充 M11 编排文档一致性：`host/adapters/action_documents.py` 提供完整文件读取、配置版本复核与快照校验；页面通过 `editor-document.js` 管理来源、草稿和请求先后次序，`editor-rails.js` 提供窄屏侧栏入口。省略快照的播放仍走原白名单；显式编排快照只在内存执行，不写角色包。回归见 `tests/integration/test_editor_documents.py`、`tests/frontend/verify_editor_documents.mjs`；浏览器实测覆盖预览/导出/下发同源、迟到响应、窄屏侧栏与实际 pyserial/TCP FRAME。该测试环境使用固件等价模拟器，不证明真实舵机轨迹。

2026-10-24 补充 M7/M14：播放工具不再仅返回假调度标记，安全回复结束后按序进入实际音频/运动队列。`tool_scope.py` 管每轮守卫与效果收集，`ai_responses.py` 管网络响应，`tool_performer.py` 管准备与核心派发；拒绝模板已合成时仍播放安全语音。证据见 `tests/unit/test_ai_tool_scope.py`、`tests/integration/test_ai_performance.py`、`tests/integration/test_ai_tools_threads.py` 及 [ADR-014](../decisions/ADR-014-deferred-media-tool-execution.md)。本轮只验证串行执行与取消，不关闭 M9 的音画同步和实物 C6。

## 3. Backlog P2（2 项，接口骨架 + Spec 条目预留）



| M | PRD 功能 | 落地文件路径（**预留位置**） | 用户可见行为（目标） | 验收方式（预留） |
|---|----------|------------------------------|----------------------|------------------|
| **M8** | 提醒任务（持久化、时区、幂等） | `host/tools/reminders.py`、`host/storage/reminders_repo.py` | 到点提醒；重启后**不重复触发**；时间不可靠时**暂停绝对时间提醒并提示校时**（不静默错发） | 假时钟推进到 `due_at` 断言只触发一次（幂等键）；重启不重复；`TIME_UNKNOWN` 状态暂停绝对提醒；PRD §5.2 M8、§8 边界「时间不可靠」 |
| **M12** | 角色资源包导入/导出（安全检查） | `host/roles/rolepack.py`（zip 安全解包/导入导出/哈希校验） | 导入新公仔角色包；恶意包被拒且**失败保留旧版本** | 构造恶意 zip（`../`、绝对路径、符号链接、超量文件、超高压缩比）断言全部拒绝；断言先解到临时目录、校验通过才**原子切换**；PRD **A8**、§5.2 M12 |

## 4. 交叉引用

- Spec：`00-architecture-spec.md` §4（目录）、§5（兼容契约）、§7.1/§7.1.1（状态机与三按键）、§10.4（已定死约束 T1–T4）、§11（E2E）。
- 选型：`10-tech-selection-matrix.md`（12 项，含版本号与锁定理由）。
- 无硬件方案：`20-mcu-simulator.md`（故障注入矩阵、回放回归）。
- 契约：`../api/openapi.yaml`（OpenAPI 3.0.3，14 paths / 14 schemas，全部 `/api/v1/` 前缀）。
- 决策：`../decisions/ADR-001..013`；未关闭事项见 `../decisions/OPEN-DECISIONS.md`。

> 判定：本表 **MVP 切片 8 项**落点齐备、验收可复现即视为「无硬件档位」交付完成；6 项 Backlog 已标预留位置，实装进后续迭代，不改动本表结构。
