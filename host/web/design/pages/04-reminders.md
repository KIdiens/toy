# 04 · 提醒管理（Reminders）

> 路由 `/reminders` ｜ 主题：浅色默认（管理页）｜ 组件基座：原生 Web Components（ADR-007，无 Node 构建）
> Token 引用一律 `var(--…)`；图标一律 Lucide 1.x 标识；本文件不得出现字面色值。

---

## 1. 信息层级

家长进入后**先看「下次会发生什么」，再决定「加/改/取消」**。自上而下：

1. **页头 + 校时提示位**——标题 + 一句白话；**当时间不可靠时**此处出现 `clock-warning`（绝对时间提醒已被暂停并提示校时），优先级最高。
2. **新建入口**——`new-reminder-button`（右上，`--accent`，本页唯一主操作）。
3. **提醒列表**（主内容）——`reminder-list` → 多条 `reminder-row`（时间 + 重复 → 内容 → 角色音色 → 状态 → 下次触发）。
4. **新建/编辑表单**（`reminder-form`，抽屉或行内展开）——≤4 个可见字段。
5. **页脚**：时区说明（存储 UTC、展示 Asia/Shanghai）+ 幂等说明。

主次：列表（3）> 校时告警（1）> 新建（2、4）> 说明（5）。

## 2. 组件清单

| 组件 | attribute / property | Lucide 图标 |
|---|---|---|
| `page-header` | attr `title`、`subtitle`；slot `actions` | `alarm-clock` |
| `clock-warning` | attr `state="unsynced"`；emit `clock-sync-open` | `clock-alert` |
| `new-reminder-button` | emit `reminder-new` | `plus` |
| `reminder-list` | prop `items[]`；emit `row-toggle`、`row-cancel` | `list` |
| `reminder-row` | attr `reminder-id`、`status`、`timezone`；prop `reminder`（`Reminder` schema）；emit `row-toggle`、`row-cancel`、`row-edit` | `bell` |
| `status-pill` | attr `status`=`pending｜fired｜missed｜cancelled` | `clock`、`circle-check`、`triangle-alert`、`circle-slash` |
| `next-fire-readout` | attr `due-at`、`enabled` | `timer` |
| `reminder-form` | attr `mode="create｜edit"`；prop `draft`；emit `form-submit`、`form-cancel` | `square-pen` |
| `time-field` | attr `value`；emit `time-change`（本地时间 → 提交前转 UTC） | `clock` |
| `repeat-picker` | attr `selected`=`once｜daily｜weekdays`（**≤3 选项**）；emit `repeat-change` | `repeat` |
| `voice-select` | attr `role-id`；prop `roles[]`；emit `voice-change` | `user-round` |
| `inline-confirm` | attr `label`；emit `confirm`、`cancel` | `trash-2`、`check`、`x` |
| `empty-state` | attr `title`、`hint`；slot `actions` | `bell-off` |
| `loader-skeleton` | attr `rows` | `loader-circle` |
| `toast-stack` | prop `toasts[]` | `circle-check`、`circle-x`、`info` |
| `theme-toggle` | attr `theme`；emit `theme-change` | `sun` / `moon` |

## 3. 交互状态

- **空态**：`empty-state` + `bell-off`，文案具体可用：「还没有提醒。设一个试试——比如每天晚上 8 点提醒刷牙。」+ 主按钮「新建提醒」。
- **加载态**：列表区 `loader-skeleton`；提交表单时按钮进入 loading 并禁用重复提交。
- **错误态**：
  - `GET /api/v1/reminders` 失败 → 中性提示 + 重试；保留旧渲染并标注可能过期。
  - `POST /api/v1/reminders` 返回 **422**（时间模糊或超限）→ **不关闭表单**，`time-field` 内联错误文案（如「请指明是上午还是下午」），焦点移到该字段。
  - 网络失败 → `toast-stack` 显 `circle-x` + 「创建失败，未保存」。
- **离线态（预期常态）**：
  - 本地已存的提醒仍可查看；横幅轻提示「离线模式」。
  - **关键**：`/status.network_online=false` 且**时钟不可靠**时，`clock-warning` 出现并把 `pending` 提醒标注「已暂停，等待校时」——**不得静默错发**。
  - 断网时创建请求不可达 → 明确提示「离线不可创建，请先连接本机服务」，不进入假成功。
- **边界态**：
  - 提醒 > 100 条 → 虚拟滚动。
  - `status=missed`（应触发但错过）→ `status-pill` 用 `--warn` + 文字「已错过」+ 说明「设备当时未开机」。
  - `status=cancelled` → 行整体弱化（`--meta`）并排在列表尾部。
  - `text` 超 200 字符（契约上限）→ 输入框计数提示，超出即阻断。
  - 重叠提醒（同日同时刻）→ 行内提示「与另一条提醒时间重叠」。
  - 无 `role-id` 可选（未注册角色）→ `voice-select` 显示 `—` + 说明「先用默认音色」。

## 4. 对应 API

| 端点 | 触发 | 渲染要点 |
|---|---|---|
| `GET /api/v1/reminders` | 首屏 / 刷新 | 可传 `profile_id`。返回 `Reminder[]`（`id/profile_id/text/due_at_utc/timezone/repeat_rule/status/last_fired_at/idempotency_key`）→ `reminder-list`；展示用 Asia/Shanghai，存储为 UTC |
| `POST /api/v1/reminders` | 表单提交 | 带 `request_id` + `profile_id` + `text` + `due_at_utc` + `timezone` + `repeat_rule` + `idempotency_key`。**201** → 插入列表并高亮新行；**422** → 内联澄清，表单保持打开 |
| `DELETE /api/v1/reminders/{id}` | 取消（`inline-confirm` 后） | `id` 为路径参数。成功 → 行状态转 `cancelled`（软取消）而非直接消失，可回溯 |
| `GET /api/v1/roles` | 表单 `voice-select` | 提供音色选择（`RoleSummary.role_id`/`tts_voice`） |

- 写操作带 `request_id`；创建带 `idempotency_key`（唯一索引）**防重复创建**——UI 侧在提交期间禁用按钮，并要求同一表单生成稳定 key。
- 统一信封 `{code,data,message}`；非 2xx 一律不进入成功态。

## 5. 响应式

- **主题**：默认浅色；`theme-toggle` 写 `<html data-theme="dark">`。
- ≥ lg：内容列 `--container-read` 720px；列表行单行六段；表单在右侧 `--rail-right` 300px 抽屉常驻。
- md–lg：表单改底部抽屉。
- < md：列表行两行堆叠（时间+重复 / 内容+状态）；`new-reminder-button` 固定右下（FAB）；表单全屏抽屉；`repeat-picker` 三选项仍保持单选，不换行。
- 触摸目标 ≥ 44px；`time-field` 用原生 `input[type=time]`（触发系统时间键盘）。

## 6. 无障碍

- 焦点顺序：页头 → 校时告警（若有）→ 新建 → 列表逐行 → 行操作 → 表单（时间 → 重复 → 内容 → 音色 → 提交）。
- 表单每个字段有**常显 `label`**（禁止用 placeholder 当 label）；错误文案与字段用 `aria-describedby` 关联，且**错误靠近字段**而非统一顶部。
- `status-pill` 语义不单靠颜色：色点 + 文字（待触发/已触发/已错过/已取消）。
- `clock-warning` 用 `role="status"` + `aria-live="polite"`。
- `reminder-row` 的启用/暂停用 `role="switch"` + `aria-checked`。
- 对比度 ≥ 4.5:1；焦点环 `var(--focus-ring)`。

## 7. 验收对应

> **诚实说明**：提醒任务（M8）在 Spec §2.2 属 **P2 Backlog（接口预留、本阶段不实现）**，§9 的 AC-01..AC-26 中**没有**提醒专条。故本页不硬套 AC 编号，改为对齐 §10「边界与约束」中与之直接相关的两条：

| Spec 条款 | 本页体现 |
|---|---|
| §10 时间不可靠 | 冷启动无法确认时间时，`clock-warning` 出现并**暂停绝对时间提醒** + 提示校时，不静默错发 |
| §10 离线降级 | 断网仍可查看已存提醒；创建不可达时明确提示，不静默失败 |
| §5 契约约束 2 | 创建请求带 `request_id`；`idempotency_key` 防重复 |
| §5 契约约束（错误码） | `POST /reminders` 的 422 走「澄清」分支，不当作 500 处理 |
