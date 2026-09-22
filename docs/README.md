# 架构文档索引（toy/host）

> 负责人：高见远（首席架构师）· 编制日期：2026-09-21
> 本目录是 `toy/host` 的架构侧唯一真源。实现以这些文档为准；实现与文档冲突时**先改文档再改代码**。

## 阅读顺序

| 文档 | 内容 | 给谁看 |
|------|------|--------|
| `architecture/00-architecture-spec.md` | 分层、目录结构（含 ≤300 行/入口只装配的硬约束）、状态机与串口协议、设计令牌、**技术约束与不可行警告**、out-of-scope、端到端验证步骤 | 全体；开发按此实现 |
| `architecture/10-tech-selection-matrix.md` | 12 项选型对比（每项 ≥3 方案 + 实际版本号 + 评分 + 锁定理由） | 开发/运维 |
| `architecture/20-mcu-simulator.md` | 无硬件可验证方案：MCU 模拟器（与固件逐项对照）、三来源串口、虚拟时钟、故障注入矩阵、回放 | 开发/测试 |
| `architecture/30-traceability.md` | PRD 功能 **M1–M14** → 落地文件 → 用户可见行为 → 验收方式（追溯表） | 全体；验收/交接 |
| `architecture/40-storage-design.md` | 存储层契约：数据库门面、迁移策略、四仓储函数签名、`settings` KV 定性与数据纪律 | 后端；验收 |
| `api/openapi.yaml` | 网页管理 API 契约（OpenAPI 3.0.3，前后端唯一依据） | 前端/后端 |
| `decisions/ADR-001..012` | 每条选型一份 MADR 决策记录 | 追溯 |
| `decisions/OPEN-DECISIONS.md` | 未关闭决策登记（主动不做/无法本阶段关闭项），不混入已 Accepted 的 ADR | 全体 |

## 决策一览

| ADR | 决策 | 锁定 |
|-----|------|------|
| ADR-001 | 主机运行时 | CPython 3.13 + venv |
| ADR-002 | 状态机 | 自研显式状态表（>12 状态升级 python-statemachine） |
| ADR-003 | 并发模型 | 领域线程 + 队列 + 单线程确定性核心 |
| ADR-004 | 串口抽象 | MotionLink + Serial/Sim/Replay（socket:// 复用） |
| ADR-005 | 本地数据库 | stdlib sqlite3 + user_version 迁移 |
| ADR-006 | 网页后端 | FastAPI 0.128 + uvicorn 0.53 |
| ADR-007 | 前端 | 原生 ES 模块 + Web Components，无构建 |
| ADR-008 | 音频后端 | sounddevice 0.5.6 + soundfile 0.14 + Null 后端 |
| ADR-009 | 图标库（P0 锁定） | Lucide 1.x（ISC），全项目唯一 |
| ADR-010 | 云端 AI | Provider Protocol + Stub/Qwen 切换 |
| ADR-011 | 无硬件验证 | MCU 模拟器 + 虚拟时钟 + 回放 |
| ADR-012 | 颜色/设计令牌 | 语义化 CSS 变量，禁硬编码色值 |

## P0 规则（不可协商）

1. 禁止 emoji 充当功能图标；唯一图标库 = **Lucide**。
2. 禁止紫色→粉色渐变。
3. 禁止硬编码颜色值（唯一例外 `#fff`/`#000`）。
4. 禁止 AI 模板味文案。

## 本阶段边界（一句话）

无硬件、无云端条件下**逻辑与协议全链路可运行、可确定性测试**；实物项（舵机运动/中位/电气急停/PWM 时延/ADC 窗口/温升/机械/真实云 AI）按 `00-architecture-spec.md` §10.1 保留为**待验证**，不得以模拟结果替代实物证据。

## 跨文档一致性纪律（上游真源 → 下游引用）

> 起因：多轮迭代连续暴露同一类问题——**上游改了、下游没跟**（例：`pyproject.toml` 上界改了，选型矩阵/SPEC §4 没同步；架构 §10.1 加了 C13，SPEC §12 没同步）。规则固化如下（Lead 裁定，2026-09-22）。

**上游真源**（谁改谁负责发起同步）：
- `pyproject.toml` —— 依赖版本的唯一真源
- `api/openapi.yaml` —— 接口契约的唯一真源
- `architecture/00-architecture-spec.md` —— 架构的唯一真源

**下游引用**（上游变了必须跟着变，并在下游标「依上游 X 修订」）：
- `SPEC-host-mvp.md`（唯一开发依据）、`DELIVERY.md` / `DELIVERY.html`（交付说明）、`architecture/10-tech-selection-matrix.md`（版本锚定镜像）、`decisions/OPEN-DECISIONS.md`、`architecture/30-traceability.md`

| 触发动作 | 必须连带做的事 |
|---|---|
| 改 `pyproject.toml` 的依赖版本 | 同步 ADR + 技术选型矩阵 + SPEC §4 版本锚定表 |
| 在 §10.1 增删 C 条目 | 同步 SPEC §12 判定段、DELIVERY 的 C 编号清单 |
| 在 `OPEN-DECISIONS.md` 增删 OD | 同步 DELIVERY 的开放决策导航 |
| 任何全局编号体系变动 | 做一次**全仓 grep 对账**，确认无遗漏 |

**SPEC 改动权限（两级）**：
- **跟上游走**（同步已批准变更、维护一致性）→ 直接改 + 在 SPEC §13 留变更记录；
- **决定上游该是什么**（改语义 / 范围 / AC 条目 / 「明确不做」清单 / API 契约 / 表结构 / 验收判据）→ **先经 Lead 批准**。

一句话判据：**跟着上游走 → 直接改 + 留痕；决定上游该是什么 → 先问 Lead。**

**日期口径**：文档头保留**基线日期**（该决策何时被接受）；changelog / 修订行用**真实修订日期**（该行何时发生）。两者语义不同，不做追溯性全量统一，只对确有错误的具体行勘误。
