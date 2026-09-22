# 03 · 记忆管理（Memories）

> 路由 `/memories` ｜ 主题：浅色默认（管理页）｜ 组件基座：原生 Web Components（ADR-007，无 Node 构建）
> Token 引用一律 `var(--…)`；图标一律 Lucide 1.x 标识；本文件不得出现字面色值。

---

## 1. 信息层级

家长进入后**先看懂「玩具记住了什么」，再决定「删什么」**。自上而下：

1. **页头 + 白话说明**——一句话解释这块是干嘛的，并给隐私承诺：「这些是玩具记住的关于你们的对话。删除后它不会再提起；这里只显示文字，不保存原始录音。」
2. **工具行**——`search-field` + `kind-filter`（类型筛选）+ `memory-toggle`（记忆开关状态只读呈现）。
3. **记忆列表**（主内容）——`memory-list` → 多条 `memory-row`。列：内容摘要 / 类型 / 来源会话时间 / 状态 / 操作。**用列表而非卡片网格**（信息密度优先、可扫读）。
4. **底部危险操作**——`clear-all-button`（清空全部，独立分组、`--danger` 描边，置底）。
5. **页脚**：记忆条数、上次写入时间、隐私说明链接。

主次：列表（3）> 说明（1）> 筛选（2）> 危险操作（4）。

## 2. 组件清单

| 组件 | attribute / property | Lucide 图标 |
|---|---|---|
| `page-header` | attr `title`、`subtitle`；slot `actions` | `brain` |
| `privacy-note` | attr `tone="info"`；slot 默认（白话文案） | `info` |
| `search-field` | attr `placeholder`；emit `search-input`（防抖） | `search` |
| `kind-filter` | attr `selected`；prop `kinds[]`（偏好/事实/事件）；emit `filter-change` | `filter` |
| `memory-toggle` | attr `enabled`（只读态：记忆开关）；**不可在此页写** | `toggle-right` |
| `memory-list` | prop `items[]`；emit `row-delete` | `list` |
| `memory-row` | attr `memory-id`、`kind`、`confirmed`、`created-at`；prop `memory`（`Memory` schema）；emit `row-delete`、`row-expand` | `circle-check`（已确认）/ `circle-dot` |
| `kind-badge` | attr `kind`=`preference｜fact｜event` | `tag` |
| `inline-confirm` | attr `label`、`confirm-text`；emit `confirm`、`cancel` | `trash-2`、`check`、`x` |
| `clear-all-dialog` | attr `require-checkbox`；emit `cleared`、`cancel` | `triangle-alert` |
| `empty-state` | attr `title`、`hint`；slot `actions` | `inbox` |
| `loader-skeleton` | attr `rows` | `loader-circle` |
| `toast-stack` | prop `toasts[]`；emit `toast-dismiss` | `circle-check`、`circle-x`、`info` |
| `theme-toggle` | attr `theme`；emit `theme-change` | `sun` / `moon` |

## 3. 交互状态

- **空态**：无记忆时用 `empty-state` + `inbox`，文案**具体可用**：「还什么都没记住。当孩子说『记住我喜欢恐龙』时，会显示在这里。」+ 次要说明「记忆开关当前：开 / 关」（读自 `/settings.memory_enabled`）。
- **加载态**：`memory-list` 位置显示 `loader-skeleton`（对齐最终行高，避免布局抖动）。
- **错误态**：`GET /api/v1/memories` 失败 → 列表区中性提示「读取记忆失败」+ `refresh-cw` 重试；**不清空已有渲染**，标注「数据可能过期」。
- **离线态（预期常态）**：
  - 本页为**纯本地数据**（SQLite），离线仍可查看与删除；横幅仅在需要时轻提示「离线模式 · 数据来自本机数据库」，文案中性。
  - 若删除请求因后端不可用失败 → `toast-stack` 显 `circle-x` + 「删除未生效，请重试」，**行保持原状**（不乐观删除，避免「看着删了其实没删」）。
- **边界态**：
  - 记忆 > 100 条 → 列表虚拟滚动（窗口化）；筛选/搜索在后端以 `kind`/`profile_id` 参数下推。
  - 超长 `value` → `memory-row` 单行截断（`title` 全文）+ `row-expand` 展开。
  - `confirmed=false` 的未确认记忆 → `kind-badge` 旁加次要标记「待确认」，视觉弱化。
  - 无搜索结果 → `empty-state` 文案「没有匹配的记忆。试试换个关键词或清除筛选。」

## 4. 对应 API

| 端点 | 触发 | 渲染要点 |
|---|---|---|
| `GET /api/v1/memories` | 首屏 / 搜索 / 筛选 | 可传 `profile_id`、`kind`（query）。返回 `Memory[]`（`id/profile_id/kind/value/confirmed/created_at/expires_at`）→ `memory-list` |
| `DELETE /api/v1/memories/{id}` | 单条删除（`inline-confirm` 确认后） | `id` 为**路径参数**。成功 → 从列表移除该行 + `toast-stack` 确认（同时后端会清会话缓存与摘要，界面文案提示「新会话不会再提起」） |
| `GET /api/v1/settings` | 首屏读取 `memory_enabled` | 驱动 `memory-toggle` 的只读状态展示（**本页不修改**，修改在 `/settings`） |

- 写操作（DELETE）带 `request_id`。
- 错误处理：统一信封 `{code,data,message}`；删除失败（非 200）→ 不改变列表，`toast-stack` 显示 `message`。
- **不返回原始录音**：契约与数据纪律要求接口只给文本；界面不得渲染音频路径或录音。

## 5. 响应式

- **主题**：默认浅色（`<html>` 无 `data-theme`）；切换靠 `theme-toggle` 写 `<html data-theme="dark">`。
- ≥ lg：内容列宽 `--container-read`（720px）居中；列表行「摘要 | 类型 | 时间 | 状态 | 操作」单行五列。
- md–lg：合并「时间」与「状态」为一列（上下叠放）。
- < md：行改为**两行卡片式堆叠**（摘要 + 次级元信息行），操作收敛为行尾溢出菜单；`search-field` 与 `kind-filter` 上下堆叠；危险操作仍置底。
- 触摸目标 ≥ `var(--touch-min)`；行内确认按钮间距 ≥ 8px。

## 6. 无障碍

- 焦点顺序：页头 → 搜索 → 类型筛选 → 列表（逐行）→ 行操作（展开 → 删除）→ 清空全部 → 页脚。
- `memory-list` 用 `role="list"`，`memory-row` 用 `role="listitem"`；每行可 `Enter` 展开。
- `inline-confirm` 出现时把焦点移到确认按钮，取消后焦点归还触发行。
- `clear-all-dialog` 用 `role="dialog"` + `aria-modal="true"`，打开时焦点陷阱在弹窗内，`Esc` 关闭；勾选框有显式 `label`（不可仅用颜色/位置表示）。
- 删除等破坏性动作**不单靠颜色**：`--danger` 描边 + 文字「删除」+ `trash-2` 图标三重编码。
- 对比度 ≥ 4.5:1；焦点环 `var(--focus-ring)`。

## 7. 验收对应

| AC | 本页体现 |
|---|---|
| AC-12 记忆持久化 | 重开连接后 `GET /memories` 仍可读回（空态/列表均以真实数据为准） |
| AC-13 删除生效 | `DELETE /memories/{id}` 成功后行消失；界面文案明确「新会话不再注入，含历史与摘要缓存」 |
| AC-14 记忆开关 | `memory-toggle` 只读呈现 `memory_enabled`；关闭时页头提示「当前不会写入新记忆」，修改入口指向 `/settings` |
