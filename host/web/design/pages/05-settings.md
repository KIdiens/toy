# 05 · 设置（Settings）

> 路由 `/settings` ｜ 主题：浅色默认（管理页）｜ 组件基座：原生 Web Components（ADR-007，无 Node 构建）
> Token 引用一律 `var(--…)`；图标一律 Lucide 1.x 标识；本文件不得出现字面色值。
> 危险区永远置底独立分组；恢复出厂需输入 `RESET` + 5 秒冷却；配网密码**永不回显**。

---

## 1. 信息层级

家长进入后是「逐块确认的系统台账」：**先看日常可调项（音量/语言/配网），再决定边界项（儿童模式/内容审核），最后才是不可逆的危险区**。五个子分区自上而下、彼此独立成组，**互不嵌套**：

1. **页头**——标题 + 一句白话 + 右侧 `theme-toggle`（浏览器偏好模式非设置项，仅切 `data-theme`）。
2. **音量与语言**——`volume-select` + `language-select` + 提示音开关。日常高频，置顶。
3. **网络与配网**——`wifi-config`（SSID + 密码不回显 + 主机服务地址）+ 顶部连接态摘要。离线时中性说明。
4. **儿童模式**——`child-mode-panel`（总开关 + 夜间不主动发声 + 记忆最小化 + 录音可见指示 + 访客不持久）。边界项，独立成组。
5. **内容审核**——`content-review-panel`（输出审核前置说明 + 固定拒绝模板）+ `content-import-card`（资源包导入，走 `/content/import`）。
6. **高级 · 危险区**——`danger-zone`（独立分隔 + `--danger` 标题 + 上方留白），内含 `factory-reset-dialog`（输入 `RESET` + 5 秒冷却）。

主次：日常可调（2、3）> 边界项（4、5）> 危险区（6）。**危险区永远排最后，且永远视觉独立。**

## 2. 组件清单

| 组件 | attribute / property | Lucide 图标 |
|---|---|---|
| `page-header` | attr `title`、`subtitle`；slot `actions` | `settings` |
| `settings-section` | attr `title`、`hint`、`danger`(boolean)；slot 内容 | `volume-2`、`wifi`、`baby`、`scan-eye`、`triangle-alert` |
| `volume-select` | attr `value`（0–100）；prop `settings`；emit `settings-patch` | `volume-2` |
| `language-select` | attr `value`；emit `settings-patch` | `languages` |
| `cue-sound-toggle` | attr `checked`；emit `settings-patch` | `bell-ring` |
| `wifi-config` | prop `settings`；emit `settings-patch`、`wifi-scan` | `wifi` |
| `password-field` | attr `name`、`configured`(boolean)；**永不回显**：已配置时显占位「已设置 · 未修改」，仅在新输入时提交 | `lock`、`key-round` |
| `host-endpoint-field` | attr `value`（`http://127.0.0.1:8000`）；emit `settings-patch` | `server` |
| `conn-summary` | attr `network-online`、`conn-state` | `wifi-off`、`wifi`、`plug-zap` |
| `child-mode-panel` | prop `settings`；emit `settings-patch` | `baby`、`shield-check` |
| `night-quiet-toggle` | attr `checked`；emit `settings-patch` | `moon-star` |
| `memory-min-toggle` | attr `checked`；emit `settings-patch` | `eraser` |
| `record-indicator-toggle` | attr `checked`；emit `settings-patch` | `mic`、`eye` |
| `content-review-panel` | attr `enabled`（只读展示，审核前置不可关） | `scan-eye`、`shield-check` |
| `content-import-card` | emit `import-select`、`import-submit` | `upload`、`file-check` |
| `import-result-list` | prop `results[]` | `circle-check`、`triangle-alert`、`circle-x` |
| `danger-zone` | slot 内容 | `triangle-alert` |
| `factory-reset-dialog` | attr `open`；internal `confirm-word`、`cooldown`；emit `reset-confirm`、`reset-cancel` | `rotate-ccw` |
| `confirm-word-field` | attr `expect="RESET"`；emit `confirm-word-change` | `square-pen` |
| `countdown-button` | attr `cooldown-seconds`、`disabled`；emit `fire` | `timer` |
| `theme-toggle` | attr `theme`；emit `theme-change` | `sun` / `moon` |
| `loader-skeleton` | attr `rows` | `loader-circle` |
| `toast-stack` | prop `toasts[]` | `circle-check`、`circle-x`、`info` |

## 3. 交互状态

- **加载态**：首屏 `GET /api/v1/settings` 未返回前，各卡片显示 `loader-skeleton`；**不做乐观写入**——`PATCH` 成功前不改变已显示值。
- **保存态**：单项 `PATCH /api/v1/settings` 触发后，该字段进入 loading（控件禁用），成功 → `toast-stack` 显 `circle-check`「已保存」；失败 → 回滚到旧值 + `circle-x`「未保存，请重试」。
- **错误态**：
  - `GET /settings` 失败 → 中性提示 + 「重试」，各卡片显示 `—`（不用 0 冒充）。
  - `PATCH /settings` 返回 **422**（如音量越界/语言不支持）→ **错误靠近字段**，内联文案，焦点移到该字段。
  - `POST /api/v1/content/import` 返回 **400**（路径穿越 / 符号链接 / 压缩炸弹 / 超量）→ `import-result-list` 用 `triangle-alert`/`circle-x` 逐条列出**拒绝原因**，并明确「**旧版本已保留，未被替换**」。
- **离线态（本阶段预期常态）**：
  - 界面**照常可编辑**；`conn-summary` 显 `wifi-off` 中性文案「未连接设备服务」，给两条出路：「重试」/「连接指引」。
  - `PATCH` 不可达 → 明确「离线无法保存到本机服务」，**不进入假成功**，值回滚。
  - 配网区块离线时给一段白话指引（如何启动本机服务 / 打开回环地址），不把离线当错误报错。
- **危险区状态机（不可逆操作三闸）**：
  1. `danger-zone` 默认折叠，需显式展开才露出 `factory-reset-dialog` 入口。
  2. 弹窗内 `confirm-word-field` 必须**逐字输入 `RESET`**（大小写敏感），否则 `countdown-button` 保持禁用。
  3. 输入正确后按钮进入 **5 秒冷却**（`countdown-button` 显示剩余秒数，期间可取消）；冷却结束才可点击执行。
  - 冷却期间关闭弹窗 → 冷却与已输入词**清零**，重开需重新走三闸。
- **密码字段态**：`password-field` **永不回显明文**。已配置 → 占位「已设置 · 未修改」（值不带出）；仅当用户输入新值时随 `PATCH` 提交；提供「清除密码」为独立次要操作（需二次确认）。
- **边界态**：
  - 音量控件输入超范围 → 钳制到合法区间并提示。
  - `host-endpoint-field` 非 `http://127.0.0.1`/非法 URL → 内联错误（本阶段仅绑回环，开放局域网需登录）。
  - 语言值不被支持 → `language-select` 保留原值 + 说明。
  - `settings` 键缺失 → 各控件显示默认态并在提示中说明「使用默认值」。

## 4. 对应 API

| 端点 | 触发 | 渲染要点 |
|---|---|---|
| `GET /api/v1/settings` | 首屏 / 重试 | 返回 `Settings`（音量 / 语言 / 配网 / 儿童模式开关等）。**密码字段以"已配置"布尔回传，绝不含明文**。`—` 表示无数据 |
| `PATCH /api/v1/settings` | 任一设置项变更 | 带 `request_id` + `SettingsPatch`（仅提交变化字段）。**200** → toast「已保存」；**422** → 内联澄清；离线不可达 → 回滚 + 明确提示 |
| `POST /api/v1/content/import` | `content-import-card` 提交 | multipart；限大小 / 路径 / 签名后导入。**201** → 成功列出；**400** → 逐条拒绝原因，**保留旧版本** |
| `GET /api/v1/status` | `conn-summary` 复用 | 读 `network_online` / 当前连接态，决定离线文案分支 |

- 写操作统一带 `request_id`；统一信封 `{code,data,message}`；非 2xx 一律不进入成功态。
- `/status` 及任何端点**不得返回密钥**——本页对密钥只呈现「是否已配置」。

## 5. 响应式

- **主题**：默认浅色（家长阅读）；`theme-toggle` 写 `<html data-theme="dark">`，仅影响界面配色，不改变设置语义。
- ≥ lg：单列阅读流，内容列 `--container-read` 720px 居中；子分区为独立面板，间距 `--section-y`。
- md–lg：单列铺满，面板间距收窄。
- < md：单列堆叠；`danger-zone` 仍独立置底并加**额外顶部分隔留白**；`factory-reset-dialog` 改全屏抽屉；`theme-toggle` 收进行头。
- 触摸目标 ≥ 44px；音量用原生 `input[type=range]` 或步进；数字输入触发数字键盘。

## 6. 无障碍

- 焦点顺序：页头 → `theme-toggle` → 音量语言 → 配网 → 儿童模式 → 内容审核 → 内容导入 → **危险区**（最后）。危险区**不得**在 Tab 顺序中提前出现。
- 每个设置项有**常显 `label`**；`password-field` 的「已设置 · 未修改」用 `aria-describedby` 说明，禁止仅凭 placeholder 传达状态。
- `settings-section[danger]` 用 `role="group"` + `aria-labelledby`，并配**文字**「危险区 · 不可逆」而非仅靠红色。
- `factory-reset-dialog` 为 `role="dialog" aria-modal="true"`，打开时聚焦 `confirm-word-field` 并**焦点陷阱**；冷却倒计时用 `aria-live="polite"` 播报剩余秒数；`Esc` 关闭并清零。
- 开关用 `role="switch"` + `aria-checked`；`content-review-panel` 的「审核前置」为只读项，用 `aria-readonly` 明确不可关。
- 对比度 ≥ 4.5:1；`--danger` 不与 `--bg` 灰叠；焦点环 `var(--focus-ring)`；全部动效受 `prefers-reduced-motion` 约束。

## 7. 验收对应

| Spec 条款 | 本页体现 |
|---|---|
| §9 AC-19 审核前置 | `content-review-panel` 明确输出审核在**合成/播放之前**完成，界面只读展示、**不可关闭**，绝不「先说出来再撤回」 |
| §9 AC-20 固定拒绝模板 | `content-review-panel` 说明涉危险现实操作引导找成年人、索要住址/学校/密码固定拒绝，模板不可被用户改写 |
| §9 AC-25 资源包安全 | `content-import-card` + `import-result-list` 对 `../`、绝对路径、符号链接、超量文件、高压缩比逐条拒绝，失败**保留旧版本** |
| §9 AC-14 记忆开关 | `child-mode-panel` 内 `memory-min-toggle` 与记忆开关一致：关闭时「记住……」不写入 |
| §10 儿童模式 | 双层审核在播放前完成；夜间不主动发声（`night-quiet-toggle`）；记忆最小化/访客不持久；录音有可见指示（`record-indicator-toggle`）；静音真停采集 |
| §10 离线降级 | 页面照常可编辑；配网/保存不可达时明确提示 + 「重试 / 连接指引」两条出路，不静默失败 |
| §7 界面硬约束 | 危险区永远置底独立分组；恢复出厂需输入 `RESET` + 5 秒冷却；无数据显 `—` 不用 0 |
| §5 契约约束 2 / 4 | 写操作带 `request_id`；`/settings` 不回显密码，本页对密钥仅呈现「已配置」布尔 |
